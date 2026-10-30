"""Latest CAMS global forecast of organic matter aerosol optical depth (550 nm).

Same source and request as the AOD_PM project: dataset cams-global-atmospheric-composition-forecasts from
the Atmosphere Data Store (ADS) through `cdsapi` (credentials in ~/.cdsapirc: url
https://ads.atmosphere.copernicus.eu/api and your key). The forecast is issued at 00 and 12 UTC, 3-hourly
out to +120 h, and a cycle appears some hours after its nominal time, so the newest cycle is tried first and
older ones after it (`latest_cycles`). The field is cut to the map region, interpolated from CAMS's native
grid (about 0.4 degrees) onto the map grid and stored per cycle in a small cache file
(cams_<YYYYMMDDHH>.npz, newest only). AOD is kept in 0.02 steps in a uint8, i.e. 0 to 5.1, which is the range
of the colour scale.

Pure functions plus a `fetch` that takes the client as an argument, so it can be tested without network.
"""
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

DATASET = "cams-global-atmospheric-composition-forecasts"
VARIABLE = "organic_matter_aerosol_optical_depth_550nm"
AOD_VAR_CANDIDATES = ("omaod550", VARIABLE)
CYCLE_HOURS = (0, 12)                 # forecast cycles per day (UTC)
LEADTIME_HOURS = list(range(0, 121, 3))
STEP_Q = 0.02                         # AOD per count of the stored uint8 (0 .. 5.1)
MARGIN = 1.0                          # degrees requested beyond the region, for the interpolation
FORMAT = 1                            # bump when the cache layout changes


@dataclass(frozen=True)
class Grid:
    """Map grid: nodes at lat_n, lat_n - step, ... and lon_w, lon_w + step, ..."""
    lat_n: float
    lat_s: float
    lon_w: float
    lon_e: float
    step: float

    @property
    def ny(self):
        return round((self.lat_n - self.lat_s) / self.step) + 1

    @property
    def nx(self):
        return round((self.lon_e - self.lon_w) / self.step) + 1

    def lats(self):
        return self.lat_n - self.step * np.arange(self.ny)

    def lons(self):
        return self.lon_w + self.step * np.arange(self.nx)

    def meta(self):
        return np.array([self.lat_n, self.lat_s, self.lon_w, self.lon_e, self.step, FORMAT])


def latest_cycles(now, max_back=5):
    """Cycle times (UTC) at or before `now`, newest first, 12 h apart."""
    now = now.astimezone(timezone.utc)
    today = sorted(now.replace(hour=h, minute=0, second=0, microsecond=0) for h in CYCLE_HOURS)
    cycle = max((c for c in today if c <= now), default=today[0] - timedelta(days=1))
    out = []
    for _ in range(max_back + 1):
        out.append(cycle)
        cycle -= timedelta(hours=24 // len(CYCLE_HOURS))
    return out


def build_request(cycle, grid):
    """cdsapi request for one cycle; area is [North, West, South, East]."""
    date = cycle.strftime("%Y-%m-%d")
    return {
        "variable": [VARIABLE],
        "date": [f"{date}/{date}"],
        "time": [cycle.strftime("%H:%M")],
        "leadtime_hour": [str(h) for h in LEADTIME_HOURS],
        "type": ["forecast"],
        "area": [grid.lat_n + MARGIN, grid.lon_w - MARGIN, grid.lat_s - MARGIN, grid.lon_e + MARGIN],
        "data_format": "netcdf_zip",
    }


def unwrap_if_zip(path):
    """Path of the NetCDF: the download is a zip even when NetCDF is requested."""
    path = Path(path)
    with open(path, "rb") as f:
        if f.read(2) != b"PK":
            return path
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".nc")]
        if not names:
            raise RuntimeError(f"{path} is a zip without a .nc file")
        target = path.with_name("cams_unzipped.nc")
        with zf.open(names[0]) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return target


def _valid_times(ds, n):
    """Valid time of every step, whichever way the file packages the forecast time axis."""
    step_dim = next((d for d in ("step", "forecast_period") if d in ds.coords), None)
    if "valid_time" in ds.coords:
        v = pd.to_datetime(np.asarray(ds["valid_time"].values).reshape(-1))   # 2-D (reference x lead) when 1 reference
    elif "time" in ds.coords and np.issubdtype(ds["time"].dtype, np.datetime64):
        v = pd.to_datetime(np.asarray(ds["time"].values).reshape(-1))
    elif "forecast_reference_time" in ds.coords and step_dim is not None:
        ref = pd.to_datetime(np.asarray(ds["forecast_reference_time"].values).reshape(-1)[0])
        v = ref + pd.to_timedelta(np.asarray(ds[step_dim].values).reshape(-1))
    else:
        raise RuntimeError(f"cannot find the forecast valid time among {list(ds.coords)}")
    if len(v) != n:
        raise RuntimeError(f"{len(v)} valid times for {n} steps: refusing to misalign them")
    return pd.DatetimeIndex(v)


def read_forecast(path, grid):
    """(valid times, AOD float32 (steps, ny, nx)) on the map grid, sorted by valid time."""
    with xr.open_dataset(path) as ds:
        var = next((n for n in AOD_VAR_CANDIDATES if n in ds.data_vars), None)
        if var is None:
            raise RuntimeError(f"no organic matter AOD variable among {list(ds.data_vars)}")
        da = ds[var]
        lat = next(d for d in ("latitude", "lat") if d in da.dims)
        lon = next(d for d in ("longitude", "lon") if d in da.dims)
        other = [d for d in da.dims if d not in (lat, lon)]
        keep = max(other, key=lambda d: da.sizes[d]) if other else None
        for d in other:                       # drop size-1 reference-time style dimensions
            if d != keep:
                da = da.isel({d: 0})
        if keep is None:
            da = da.expand_dims("step")
            keep = "step"
        n = da.sizes[keep]
        valid = _valid_times(ds, n)
        da = da.transpose(keep, lat, lon).sortby(lat).sortby(lon)
        lons = da[lon].values
        if lons.min() < 0 <= grid.lon_w:      # file in -180..180, grid in 0..360 terms: shift
            da = da.assign_coords({lon: (da[lon] % 360)}).sortby(lon)
        out = da.interp({lat: grid.lats()[::-1], lon: grid.lons()}, method="linear").values[:, ::-1, :]
    order = np.argsort(valid.values)
    out = np.nan_to_num(out[order].astype("float32"), nan=0.0)
    return valid[order], out


def quantise(aod):
    """AOD -> uint8 counts of STEP_Q (saturates at 5.1)."""
    return np.clip(np.round(np.asarray(aod) / STEP_Q), 0, 255).astype("uint8")


def cache_file(cache_dir, cycle):
    return Path(cache_dir) / f"cams_{cycle:%Y%m%d%H}.npz"


def run_of(path):
    return datetime.strptime(Path(path).stem.split("_")[1], "%Y%m%d%H").replace(tzinfo=timezone.utc)


def newest_cached(cache_dir):
    files = sorted(Path(cache_dir).glob("cams_*.npz"))
    return files[-1] if files else None


def save_cache(path, steps, aod_q, grid):
    path = Path(path)
    tmp = path.parent / (path.name + ".tmp")           # atomic: a killed download must not leave a corrupt file
    with open(tmp, "wb") as f:
        np.savez_compressed(f, steps=np.array(steps, "int16"), aod=aod_q, region=grid.meta())
    tmp.replace(path)


def load_cache(path, grid):
    """{'steps': [...], 'aod_q': uint8 (steps, ny, nx)} or None when written for another grid/layout."""
    z = np.load(path)
    if not np.array_equal(z["region"], grid.meta()):
        return None
    return {"steps": z["steps"].tolist(), "aod_q": z["aod"]}


def fetch(cache_dir, grid, now=None, client=None, max_back=5, log=print):
    """Make sure the cache holds the newest published cycle; return its file.

    A cached cycle is used without a request. For a newer one the cycles are tried newest first (it may not
    be published yet) until a download works. `client` is a cdsapi.Client (created on demand).
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    last = None
    for cycle in latest_cycles(now, max_back):
        cached = cache_file(cache_dir, cycle)
        if cached.exists() and load_cache(cached, grid) is not None:
            log(f"CAMS cycle {cycle:%Y-%m-%d %H}Z already cached")
            _prune(cache_dir, cached, log)
            return cached
        log(f"CAMS: trying cycle {cycle:%Y-%m-%d %H}Z")
        try:
            if client is None:
                import cdsapi                      # needs ~/.cdsapirc (ADS url + key)
                client = cdsapi.Client()
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "cams.nc"
                client.retrieve(DATASET, build_request(cycle, grid)).download(str(target))
                valid, aod = read_forecast(unwrap_if_zip(target), grid)
        except Exception as e:                     # not published yet, no credentials, queue problem, ...
            log(f"  not available: {e}")
            last = e
            continue
        hours = (valid - pd.Timestamp(cycle.replace(tzinfo=None))) / pd.Timedelta(hours=1)
        steps = [int(round(h)) for h in hours]
        save_cache(cached, steps, quantise(aod), grid)
        _prune(cache_dir, cached, log)
        log(f"CAMS cycle {cycle:%Y-%m-%d %H}Z cached ({len(steps)} steps, max AOD {aod.max():.2f})")
        return cached
    raise RuntimeError(f"no CAMS cycle available in the last {max_back + 1}: {last}")


def _prune(cache_dir, keep, log):
    for old in Path(cache_dir).glob("cams_*.npz"):
        if old != keep:
            old.unlink()
            log(f"CAMS: removed old cache {old.name}")
