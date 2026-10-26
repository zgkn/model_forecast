#!/usr/bin/env python3
"""Interactive 925 hPa wind map over southeast Asia from the latest ECMWF open-data runs (IFS and AIFS).

Downloads u/v at 925 hPa for every forecast step of the latest 00/06/12/18 UTC
run of each model, and writes one self-contained Folium HTML page with a tab per
model and a time slider over the forecast steps.

    python wind_forecast.py                    # latest run, steps up to +144 h
    python wind_forecast.py --max-step 240     # longer range (00/12 UTC IFS runs)
    python wind_forecast.py --models ifs       # a single model
    python wind_forecast.py --source google    # other open-data mirror
"""
import argparse
import base64
import io
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import folium
import numpy as np
import requests
import xarray as xr
from branca.element import Element
from ecmwf.opendata import Client
from PIL import Image
from scipy.ndimage import map_coordinates

HERE = Path(__file__).resolve().parent
LEVEL = 925
# Region (same box as the reference figure): 98-120E, 6S-8N
LAT_N, LAT_S, LON_W, LON_E = 8.0, -6.0, 98.0, 120.0
STEP = 0.25  # source grid spacing in degrees
RASTER_WIDTH = 704  # speed image width in px (~0.03 degree, cubic-interpolated)
KT = 1.943844  # m/s -> knots

# colour scale in knots: 5-kt bins, last colour is the over-range (>50 kt) extension
EDGES = list(range(5, 51, 5))
COLORS = ["#ffffff", "#d3ebea", "#a8cbe0", "#b6d99d", "#5fc45f", "#efe153",
          "#f3a35a", "#d9544f", "#b01c2e", "#d86fae", "#e9d6ee"]

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


def fetch_step(client, run, step, target):
    if target.exists() and target.stat().st_size > 0:
        return target
    try:
        client.retrieve(
            type="fc", stream="oper", levtype="pl", levelist=LEVEL, param=["u", "v"],
            date=run.strftime("%Y%m%d"), time=run.hour, step=step, target=str(target),
        )
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (403, 404):
            return None  # step not published for this run
        raise
    return target


def latest_run(model, source, max_step):
    """Newest run of the model that has already published its (capped) steps."""
    client = Client(source=source, model=MODELS[model]["client_model"])
    run = client.latest(type="fc", stream="oper", step=min(max_step, 144), param="u",
                        levtype="pl", levelist=LEVEL)
    return client, run


def download(model, source, max_step, data_dir, workers):
    client, run = latest_run(model, source, max_step)
    steps = candidate_steps(model, run.hour, max_step)
    out = data_dir / f"{model}_{run:%Y%m%d%H}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"[{model}] run {run:%Y-%m-%d %H}Z, {len(steps)} steps -> {out}", flush=True)
    with ThreadPoolExecutor(workers) as pool:
        files = list(pool.map(
            lambda s: fetch_step(client, run, s, out / f"step{s:03d}.grib2"), steps))
    return run, [(s, f) for s, f in zip(steps, files) if f is not None]


def read_uv(path):
    """u, v (m/s) cropped to the region, rows north -> south, columns west -> east."""
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    ds = ds.sortby("latitude", ascending=False)
    ds = ds.sel(latitude=slice(LAT_N, LAT_S), longitude=slice(LON_W, LON_E))
    shape = (round((LAT_N - LAT_S) / STEP) + 1, round((LON_E - LON_W) / STEP) + 1)
    assert ds.u.shape == shape, ds.u.shape
    return ds.u.values.astype("float32"), ds.v.values.astype("float32")


def merc_y(lat):
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def speed_image(u, v):
    """Wind speed in knots, binned to the colour scale, as a lossless WebP data URI.

    Rows are spaced uniformly in web-mercator y so Leaflet can stretch the image
    over the region bounds; the field is cubic-interpolated from the 0.25 degree grid.
    """
    kt = np.hypot(u, v) * KT
    y_n, y_s = merc_y(LAT_N), merc_y(LAT_S)
    height = round(RASTER_WIDTH * (y_n - y_s) / math.radians(LON_E - LON_W))
    y = np.linspace(y_n, y_s, height)
    lat = np.degrees(2 * np.arctan(np.exp(y)) - math.pi / 2)
    lon = np.linspace(LON_W, LON_E, RASTER_WIDTH)
    rows, cols = np.meshgrid((LAT_N - lat) / STEP, (lon - LON_W) / STEP, indexing="ij")
    field = map_coordinates(kt, [rows, cols], order=3, mode="nearest")
    palette = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] + [184] for c in COLORS], dtype="uint8")
    palette[0, 3] = 40  # <5 kt: nearly transparent so the basemap shows
    rgba = palette[np.digitize(field, EDGES)]
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "WEBP", lossless=True, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def arrow_grid(u, v):
    """u, v on the source grid as int16 in 0.1 m/s, base64 (u block, then v block)."""
    uv = np.round(np.concatenate([u.ravel(), v.ravel()]) * 10).astype("<i2")
    return base64.b64encode(uv.tobytes()).decode()


def build_model_data(model, run, step_files):
    frames = []
    for step, path in step_files:
        u, v = read_uv(path)
        valid = run.timestamp() + step * 3600
        frames.append({
            "step": step,
            "valid": int(valid),
            "img": speed_image(u, v),
            "uv": arrow_grid(u, v),
        })
        print(f"[{model}] +{step:>3d} h processed", flush=True)
    return {
        "label": MODELS[model]["label"],
        "long": MODELS[model]["long"],
        "run": int(run.timestamp()),
        "frames": frames,
    }


def build_map(data, out_path):
    m = folium.Map(
        location=[(LAT_N + LAT_S) / 2, (LON_W + LON_E) / 2], zoom_start=6, min_zoom=4, max_zoom=10,
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
    m.get_root().header.add_child(Element("<title>ECMWF 925 hPa wind</title>"))
    m.get_root().header.add_child(Element(f"<style>{(HERE / 'viewer.css').read_text()}</style>"))
    m.get_root().html.add_child(Element((HERE / "viewer.html").read_text()))
    js = (HERE / "viewer.js").read_text()
    js = (js.replace("__MAP__", m.get_name())
            .replace("__DATA__", json.dumps(data, separators=(",", ":")))
            .replace("__GRID__", json.dumps({
                "lat0": LAT_N, "lon0": LON_W, "d": STEP,
                "nlat": round((LAT_N - LAT_S) / STEP) + 1, "nlon": round((LON_E - LON_W) / STEP) + 1,
                "bounds": [[LAT_S, LON_W], [LAT_N, LON_E]]}))
            .replace("__COAST__", (HERE / "coastlines.geojson").read_text())
            .replace("__COLORS__", json.dumps(COLORS))
            .replace("__EDGES__", json.dumps(EDGES)))
    m.get_root().script.add_child(Element(js))
    m.save(str(out_path))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    ap.add_argument("--max-step", type=int, default=144, help="last forecast step in hours (default 144)")
    ap.add_argument("--source", default="aws", help="open-data mirror: aws, google, azure, ecmwf (default aws)")
    ap.add_argument("--workers", type=int, default=12, help="parallel downloads (default 12)")
    ap.add_argument("--print-runs", action="store_true",
                    help="print the latest available run of each model as JSON and exit")
    ap.add_argument("--data-dir", type=Path, default=HERE / "data")
    ap.add_argument("--out", type=Path, default=HERE / "output" / "wind925.html")
    args = ap.parse_args()

    if args.print_runs:
        runs = {m: f"{latest_run(m, args.source, args.max_step)[1]:%Y%m%d%H}" for m in args.models}
        print(json.dumps(runs, sort_keys=True))
        return

    data, runs = {}, {}
    for model in args.models:
        run, step_files = download(model, args.source, args.max_step, args.data_dir, args.workers)
        runs[model] = f"{run:%Y%m%d%H}"
        if not step_files:
            sys.exit(f"no steps available for {model}")
        data[model] = build_model_data(model, run, step_files)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    build_map(data, args.out)
    (args.out.parent / "runs.json").write_text(json.dumps(runs, sort_keys=True))
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
