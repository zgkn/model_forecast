#!/usr/bin/env python3
"""Interactive southeast-Asia forecast map (winds, rain, humidity) from ECMWF open data (IFS and AIFS).

For the latest 00/06/12/18 UTC run of each model it fetches, per forecast step,
u/v at 925/850/700 hPa, q/t at 700 hPa (-> relative humidity) and total precipitation,
crops them to the region and keeps them in a small cache file (cache/<model>_<run>.npz;
older runs of the model are deleted). Then it writes a static site: a Folium page plus
compact binary data files (one per model and variable) that the page loads on demand
and draws on canvas layers (shading, arrows, streamlines).

    python wind_forecast.py                    # latest run (download only if not cached), steps up to +144 h
    python wind_forecast.py --no-fetch         # build from whatever is in the cache, no network to ECMWF
    python wind_forecast.py --fetch-only       # just refresh the cache
    python wind_forecast.py --max-step 240     # longer range (00/12 UTC IFS runs)
    python wind_forecast.py --models ifs       # a single model
    python wind_forecast.py --source google    # other open-data mirror

The page loads its data with fetch(), so open it through a web server, e.g.
    python -m http.server -d output
"""
import argparse
import gzip
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import folium
import numpy as np
import requests
import xarray as xr
from branca.element import Element
from ecmwf.opendata import Client

HERE = Path(__file__).resolve().parent
LEVELS = [925, 850, 700]  # wind levels (hPa); humidity is taken at RH_LEVEL
RH_LEVEL = 700
# Region: 90-128E, 12S-30N
LAT_N, LAT_S, LON_W, LON_E = 30.0, -12.0, 90.0, 128.0
STEP = 0.25  # source grid spacing in degrees
NY = round((LAT_N - LAT_S) / STEP) + 1
NX = round((LON_E - LON_W) / STEP) + 1
FORMAT = 2  # bump when the cache layout changes
REGION = [LAT_N, LAT_S, LON_W, LON_E, STEP, FORMAT]  # stored in cache files to detect changes

# Colour scales: class i is value >= edges[i-1] (class 0 is below the first edge).
PALETTES = {
    # knots, taken from the reference figure
    "speed": {
        "unit": "knots",
        "edges": list(range(5, 51, 5)),
        "colors": ["#ffffff", "#d3ebea", "#a8cbe0", "#b6d99d", "#5fc45f", "#efe153",
                   "#f3a35a", "#d9544f", "#b01c2e", "#d86fae", "#e9d6ee"],
        "alpha": [0.16] + [0.72] * 10,
    },
    # mm in the preceding model interval (3 h for IFS, 6 h for AIFS)
    "rain": {
        "unit": "mm",
        "edges": [0.5, 1, 2, 5, 10, 20, 30, 50],
        "colors": ["#ffffff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#3182bd",
                   "#08519c", "#6a51a3", "#3f007d"],
        "alpha": [0.0] + [0.75] * 8,
    },
    # %
    "rh": {
        "unit": "%",
        "edges": [20, 30, 40, 50, 60, 70, 80, 90],
        "colors": ["#8c510a", "#bf812d", "#dfc27d", "#f6e8c3", "#f5f5f5",
                   "#c7eae5", "#80cdc1", "#35978f", "#01665e"],
        "alpha": [0.7] * 9,
    },
}

MODELS = {
    "ifs": {"client_model": "ifs", "label": "IFS", "long": "ECMWF IFS (HRES, 0.25°)"},
    "aifs": {"client_model": "aifs-single", "label": "AIFS", "long": "ECMWF AIFS Single (0.25°)"},
}


def candidate_steps(model, hour, max_step):
    """Steps the model publishes for a run hour, capped at max_step."""
    if model == "aifs":
        steps = list(range(0, 361, 6))
    elif hour in (0, 12):
        steps = list(range(0, 145, 3)) + list(range(150, 361, 6))
    else:
        steps = list(range(0, 145, 3))
    return [s for s in steps if s <= max_step]


# ---- download ----------------------------------------------------------------------------------

def fetch_step(client, run, step, tmp):
    """Download the GRIBs of one step; None when the step is not published (yet)."""
    base = dict(type="fc", stream="oper", date=run.strftime("%Y%m%d"), time=run.hour, step=step)
    jobs = {
        "uv": dict(levtype="pl", levelist=LEVELS, param=["u", "v"]),
        "qt": dict(levtype="pl", levelist=RH_LEVEL, param=["q", "t"]),
        "tp": dict(levtype="sfc", param="tp"),
    }
    files = {}
    for name, req in jobs.items():
        target = Path(tmp) / f"{name}{step:03d}.grib2"
        try:
            client.retrieve(**base, **req, target=str(target))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 404):
                return None
            raise
        files[name] = target
    return files


def latest_run(model, source, max_step):
    """Newest run of the model that has already published its (capped) steps."""
    # the client's default is 500 retries 120 s apart; S3 answers "503 Slow Down" under load, so retry fast
    client = Client(source=source, model=MODELS[model]["client_model"], maximum_retries=12, retry_after=5)
    run = client.latest(type="fc", stream="oper", step=min(max_step, 144), param="u",
                        levtype="pl", levelist=LEVELS[0])
    return client, run


def open_region(path):
    """Dataset cropped to the region, rows north -> south, columns west -> east."""
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    ds = ds.sortby("latitude", ascending=False)
    return ds.sel(latitude=slice(LAT_N, LAT_S), longitude=slice(LON_W, LON_E))


def relative_humidity(q, t, p_hpa):
    """RH (%) over water from specific humidity (kg/kg) and temperature (K)."""
    tc = t - 273.15
    es = 6.112 * np.exp(17.67 * tc / (tc + 243.5))  # hPa
    e = q * p_hpa / (0.622 + 0.378 * q)
    return np.clip(100 * e / es, 0, 100)


def read_step(files):
    """u, v (3, ny, nx) m/s, rh (ny, nx) %, accumulated rain (ny, nx) mm for one step."""
    with open_region(files["uv"]) as ds:
        ds = ds.sel(isobaricInhPa=LEVELS)
        u, v = ds.u.values.astype("float32"), ds.v.values.astype("float32")
    with open_region(files["qt"]) as ds:
        rh = relative_humidity(ds.q.values.astype("float32"), ds.t.values.astype("float32"), RH_LEVEL)
    with open_region(files["tp"]) as ds:
        tp = ds.tp.values.astype("float32")
        tp = tp * 1000 if ds.tp.attrs.get("units") == "m" else tp  # IFS: m, AIFS: kg m-2 (= mm)
    for a in (u, v):
        assert a.shape == (len(LEVELS), NY, NX), a.shape
    assert rh.shape == tp.shape == (NY, NX), (rh.shape, tp.shape)
    return u, v, rh, tp


# ---- cache -------------------------------------------------------------------------------------

def cache_file(cache_dir, model, run):
    return cache_dir / f"{model}_{run:%Y%m%d%H}.npz"


def run_of(path):
    """Run time (UTC) encoded in a cache file name."""
    return datetime.strptime(path.stem.split("_")[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)


def newest_cached(cache_dir, model):
    files = sorted(cache_dir.glob(f"{model}_*.npz"))
    return files[-1] if files else None


def save_cache(path, steps, u, v, rh, tp, max_step):
    """int16 fields: wind 0.01 m/s, rh 0.1 %, accumulated rain 0.1 mm."""
    np.savez_compressed(
        path, steps=np.array(steps, "int16"), max_step=max_step, region=np.array(REGION),
        u=np.round(u * 100).astype("int16"), v=np.round(v * 100).astype("int16"),
        rh=np.round(rh * 10).astype("int16"), tp=np.clip(np.round(tp * 10), 0, 32767).astype("int16"))


def load_cache(path):
    """Cached fields in physical units, or None when written for another region/layout."""
    z = np.load(path)
    if not np.array_equal(z["region"], REGION):
        return None
    return {"steps": z["steps"].tolist(), "max_step": int(z["max_step"]),
            "u": z["u"].astype("float32") / 100, "v": z["v"].astype("float32") / 100,
            "rh": z["rh"].astype("float32") / 10, "tp": z["tp"].astype("float32") / 10}


def prune_cache(cache_dir, model, keep):
    """Delete the model's cache files of older runs."""
    for old in cache_dir.glob(f"{model}_*.npz"):
        if old != keep:
            old.unlink()
            print(f"[{model}] removed old cache {old.name}", flush=True)


def fetch_model(model, source, max_step, cache_dir, workers):
    """Make sure the cache holds the latest run of the model; return its cache file."""
    client, run = latest_run(model, source, max_step)
    path = cache_file(cache_dir, model, run)
    if path.exists():
        cached = load_cache(path)
        if cached and cached["max_step"] >= max_step:
            print(f"[{model}] run {run:%Y-%m-%d %H}Z already cached", flush=True)
            prune_cache(cache_dir, model, path)
            return path
    steps = candidate_steps(model, run.hour, max_step)
    print(f"[{model}] run {run:%Y-%m-%d %H}Z, downloading {len(steps)} steps", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        with ThreadPoolExecutor(workers) as pool:
            files = list(pool.map(lambda s: fetch_step(client, run, s, tmp), steps))
        got = [(s, f) for s, f in zip(steps, files) if f is not None]
        if not got:
            sys.exit(f"no steps available for {model}")
        fields = [read_step(f) for _, f in got]
    cache_dir.mkdir(parents=True, exist_ok=True)
    u, v, rh, tp = (np.stack([f[i] for f in fields]) for i in range(4))
    save_cache(path, [s for s, _ in got], u, v, rh, tp, max_step)
    prune_cache(cache_dir, model, path)
    print(f"[{model}] cached -> {path} ({path.stat().st_size / 1e6:.1f} MB)", flush=True)
    return path


# ---- site --------------------------------------------------------------------------------------

def gz(arr):
    return gzip.compress(np.ascontiguousarray(arr).tobytes(), 9, mtime=0)


def write_model(model, path, max_step, data_dir):
    """Write the model's binary data files; return its manifest entry."""
    run, cached = run_of(path), load_cache(path)
    if cached is None:
        sys.exit(f"{path} was written for another region/layout; refresh the cache")
    keep = [i for i, s in enumerate(cached["steps"]) if s <= max_step]
    steps = [cached["steps"][i] for i in keep]
    u, v, rh, tp = (cached[k][keep] for k in ("u", "v", "rh", "tp"))
    tag = f"{model}_{run:%Y%m%d%H}"
    files = {}

    for li, level in enumerate(LEVELS):  # (steps, 2, ny, nx) int16, 0.1 m/s
        uv = np.stack([u[:, li], v[:, li]], axis=1)
        files[f"uv{level}"] = f"data/{tag}_uv{level}.bin.gz"
        (data_dir / f"{tag}_uv{level}.bin.gz").write_bytes(gz(np.round(uv * 10).astype("<i2")))

    files[f"rh{RH_LEVEL}"] = f"data/{tag}_rh{RH_LEVEL}.bin.gz"  # (steps, ny, nx) uint8, 1 %
    (data_dir / f"{tag}_rh{RH_LEVEL}.bin.gz").write_bytes(gz(np.round(rh).astype("uint8")))

    # rain in the preceding model interval (3 h IFS, 6 h AIFS; first step has none): uint16, 0.1 mm
    rain = np.clip(np.diff(tp, axis=0, prepend=tp[:1]), 0, None)
    files["rain"] = f"data/{tag}_rain.bin.gz"
    (data_dir / f"{tag}_rain.bin.gz").write_bytes(gz(np.round(rain * 10).astype("<u2")))

    return {
        "label": MODELS[model]["label"], "long": MODELS[model]["long"],
        "run": int(run.timestamp()), "steps": steps,
        "valid": [int(run.timestamp()) + s * 3600 for s in steps],
        "interval": [0] + [b - a for a, b in zip(steps, steps[1:])],
        "files": files,
    }


def build_site(manifest, out_path):
    m = folium.Map(
        location=[(LAT_N + LAT_S) / 2, (LON_W + LON_E) / 2], zoom_start=5, min_zoom=4, max_zoom=10,
        tiles=None, zoom_control=True, zoom_snap=0.25,
        min_lat=LAT_S - 10, max_lat=LAT_N + 10, min_lon=LON_W - 10, max_lon=LON_E + 10, max_bounds=True,
    )
    folium.TileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        name="Esri light gray", no_wrap=True, max_native_zoom=16,
        attr="Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ",
    ).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", no_wrap=True, show=False).add_to(m)
    folium.LayerControl(position="topright", collapsed=True).add_to(m)
    m.fit_bounds([[LAT_S, LON_W], [LAT_N, LON_E]])
    m.get_root().header.add_child(Element("<title>ECMWF forecast, southeast Asia</title>"))
    m.get_root().header.add_child(Element(f"<style>{(HERE / 'viewer.css').read_text()}</style>"))
    m.get_root().html.add_child(Element((HERE / "viewer.html").read_text()))
    js = (HERE / "viewer.js").read_text()
    js = js.replace("__MAP__", m.get_name()).replace("__MANIFEST__", json.dumps(manifest, separators=(",", ":")))
    m.get_root().script.add_child(Element(js))
    m.save(str(out_path))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    ap.add_argument("--max-step", type=int, default=144, help="last forecast step in hours (default 144)")
    ap.add_argument("--source", default="aws", help="open-data mirror: aws, google, azure, ecmwf (default aws)")
    ap.add_argument("--workers", type=int, default=8, help="parallel downloads (default 8)")
    ap.add_argument("--cache-dir", type=Path, default=HERE / "cache", help="where the cropped data is kept")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--no-fetch", action="store_true", help="never contact ECMWF; use the newest cached run")
    mode.add_argument("--fetch-only", action="store_true", help="refresh the cache and stop")
    ap.add_argument("--out", type=Path, default=HERE / "output" / "index.html")
    args = ap.parse_args()

    paths = {}
    for model in args.models:
        if args.no_fetch:
            paths[model] = newest_cached(args.cache_dir, model)
            if paths[model] is None:
                sys.exit(f"nothing cached for {model} in {args.cache_dir}")
        else:
            paths[model] = fetch_model(model, args.source, args.max_step, args.cache_dir, args.workers)
    if args.fetch_only:
        return

    site = args.out.parent
    data_dir = site / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for old in data_dir.glob("*.bin.gz"):
        old.unlink()
    manifest = {
        "grid": {"lat0": LAT_N, "lon0": LON_W, "d": STEP, "nlat": NY, "nlon": NX,
                 "bounds": [[LAT_S, LON_W], [LAT_N, LON_E]]},
        "levels": LEVELS, "rhLevel": RH_LEVEL, "palettes": PALETTES,
        "models": {m: write_model(m, p, args.max_step, data_dir) for m, p in paths.items()},
    }
    (data_dir / "coastlines.json").write_text((HERE / "coastlines.geojson").read_text())
    build_site(manifest, args.out)
    runs = {m: p.stem.split("_")[1] for m, p in paths.items()}
    (site / "runs.json").write_text(json.dumps(runs, sort_keys=True))
    total = sum(f.stat().st_size for f in site.rglob("*") if f.is_file())
    print(f"wrote {args.out} + {len(list(data_dir.iterdir()))} data files ({total / 1e6:.1f} MB total)")


if __name__ == "__main__":
    main()
