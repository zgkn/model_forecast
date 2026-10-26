"""Consecutive dry days (CDD) from a long-range accumulated-precipitation forecast.

Pure numpy / pandas / scipy / rasterio / xarray, no network, so it can be tested with synthetic data.

    daily_rain()        accumulated tp at the UTC midnights -> rain per UTC calendar day (mm)
    streaks()           dry-day streak engine: running maximum and first day reaching 5/10/15
    land_mask()         land cells from polygons, 4x4 sub-sampled per cell
    threshold_regions() connected regions where the longest spell reaches a level (GeoJSON)
    compute()           all of the above for one model run

Limitation: streaks are counted from day 0 of the forecast. Dry days before the forecast are
unknown, so a dry spell that is already under way is under-counted.
"""
import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd
import xarray as xr
from rasterio import features
from rasterio.transform import Affine
from scipy import ndimage

DRY_MM = 1.0           # a day with less rain than this is dry
LEVELS = (5, 10, 15)   # spell lengths (days) that get outlines
MIN_DAYS = 5           # fewer full days means this is not a long-range 00/12Z run
KM_PER_DEG = 111.32


def daily_rain(run, steps, tp, scale=1000.0):
    """Rain per UTC calendar day.

    run    run time (UTC); steps forecast hours; tp accumulated since the run start,
    shaped (len(steps), ny, nx); scale converts tp to mm (1000 for metres).
    Only steps valid at exactly 00:00 UTC are used and day i covers the date of its starting
    midnight. A run that starts at 12Z therefore loses its first 12 h (partial day). Two midnights
    that are not 24 h apart (a missing step) give a NaN day, never a difference over two days.
    Returns (dates, rain) with rain shaped (days, ny, nx), NaN where unknown.
    """
    run = pd.Timestamp(run)
    if run.tzinfo is not None:
        run = run.tz_convert("UTC").tz_localize(None)
    valid = pd.DatetimeIndex([run + pd.Timedelta(hours=int(s)) for s in steps])
    mid = np.flatnonzero((valid.hour == 0) & (valid.minute == 0))
    tp = np.asarray(tp, dtype="float64") * scale
    dates, days = [], []
    for a, b in zip(mid[:-1], mid[1:]):
        dates.append(valid[a].date())
        if valid[b] - valid[a] == pd.Timedelta(hours=24):
            days.append(np.clip(tp[b] - tp[a], 0, None))   # GRIB packing can give tiny negatives
        else:
            days.append(np.full(tp.shape[1:], np.nan))
    if len(days) < MIN_DAYS:
        raise ValueError(f"only {len(days)} full days from the run at {run:%Y-%m-%d %H}Z: "
                         f"not a long-range 00/12Z run")
    return dates, np.stack(days).astype("float32")


def streaks(dry, levels=LEVELS):
    """Dry-day streak engine over a (days, ny, nx) boolean array (True = dry).

    Returns running_max (days, ny, nx) int16 (longest spell up to and including each day) and
    first_day {level: (ny, nx) int16}, the first day index whose streak equals the level (-1 never).
    """
    dry = np.asarray(dry, dtype=bool)
    streak = np.zeros(dry.shape[1:], dtype="int16")
    best = np.zeros_like(streak)
    running = np.empty(dry.shape, dtype="int16")
    first = {lv: np.full(dry.shape[1:], -1, dtype="int16") for lv in levels}
    for t in range(dry.shape[0]):
        streak = np.where(dry[t], streak + 1, 0).astype("int16")
        best = np.maximum(best, streak)
        running[t] = best
        for lv in levels:
            first[lv] = np.where((streak == lv) & (first[lv] < 0), t, first[lv]).astype("int16")
    return running, first


def cell_edges_transform(lat0, lon0, d):
    """Affine transform of a grid whose nodes are cell centres, from the north-west cell EDGE."""
    return Affine.translation(lon0 - d / 2, lat0 + d / 2) @ Affine.scale(d, -d)


def land_mask(geometry, lat0, lon0, d, ny, nx, sub=4, min_frac=0.15):
    """True for cells where at least min_frac of a sub x sub sample grid is land.

    geometry: GeoJSON geometry (Polygon/MultiPolygon) of the land. Sampling several points per cell
    keeps small islands that a single point per cell would miss.
    """
    transform = cell_edges_transform(lat0, lon0, d) @ Affine.scale(1 / sub, 1 / sub)
    fine = features.rasterize([(geometry, 1)], out_shape=(ny * sub, nx * sub), transform=transform,
                              fill=0, dtype="uint8")
    frac = fine.reshape(ny, sub, nx, sub).mean(axis=(1, 3))
    return frac >= min_frac


def cell_area_km2(lat0, d, ny, nx):
    lat = lat0 - d * np.arange(ny)
    row = (d * KM_PER_DEG) * (d * KM_PER_DEG * np.cos(np.radians(lat)))
    return np.repeat(row[:, None], nx, axis=1)


def threshold_regions(max_cdd, first_day, dates, lat0, lon0, d, levels=LEVELS):
    """GeoJSON polygons of the regions where the longest dry spell reaches each level.

    Cells equal to the level are inside (>=); NaN is outside; 4-connected components; polygons come from
    the label raster on the cell edges (holes kept, rings closed, exactly on the grid).
    """
    ny, nx = max_cdd.shape
    area = cell_area_km2(lat0, d, ny, nx)
    transform = cell_edges_transform(lat0, lon0, d)
    out = []
    for lv in levels:
        mask = np.nan_to_num(max_cdd, nan=-1) >= lv
        labels, n = ndimage.label(mask)                      # default structure = 4-connectivity
        if not n:
            continue
        props = {}
        for k in range(1, n + 1):
            cells = labels == k
            fd = first_day[lv][cells]
            fd = fd[fd >= 0]
            props[k] = {
                "level": lv,
                "earliest": dates[int(fd.min())].isoformat(),
                "peak": f"{int(np.nanmax(max_cdd[cells]))} days",
                "area": f"{int(round(area[cells].sum()))} km2",
            }
        for geom, value in features.shapes(labels.astype("int32"), mask=labels > 0,
                                           connectivity=4, transform=transform):
            ring = lambda r: [[round(x, 4), round(y, 4)] for x, y in r]
            out.append({"type": "Feature", "properties": props[int(value)],
                        "geometry": {"type": "Polygon", "coordinates": [ring(r) for r in geom["coordinates"]]}})
    return {"type": "FeatureCollection", "features": out}


@dataclass
class Result:
    dates: list            # date of each forecast day (the day starting at that 00 UTC)
    running_max: np.ndarray  # (days, ny, nx) int16, ocean = -1
    max_cdd: np.ndarray    # (ny, nx) float32 days, NaN where not land
    first_day: dict        # {level: (ny, nx) int16, -1 = never / not land}
    regions: dict          # GeoJSON FeatureCollection
    run: pd.Timestamp


def compute(run, steps, tp, land, lat0, lon0, d, scale=1000.0, dry_mm=DRY_MM, levels=LEVELS):
    """Full CDD analysis of one run. tp: accumulated precipitation (steps, ny, nx); land: bool (ny, nx)."""
    dates, rain = daily_rain(run, steps, tp, scale)
    running, first = streaks(rain < dry_mm, levels)       # NaN is never < 1 mm: missing data is not dry
    land = np.asarray(land, dtype=bool)
    if not land.any():
        raise ValueError("no land cell in the domain")
    running = np.where(land, running, -1).astype("int16")
    first = {lv: np.where(land, f, -1).astype("int16") for lv, f in first.items()}
    max_cdd = np.where(land, running[-1], np.nan).astype("float32")
    if not np.nanmax(max_cdd) > 0:
        raise ValueError("maximum consecutive dry days is 0 everywhere: the precipitation data looks wrong")
    regions = threshold_regions(max_cdd, first, dates, lat0, lon0, d, levels)
    return Result(dates, running, max_cdd, first, regions, pd.Timestamp(run))


def to_dataset(res, lat0, lon0, d, dry_mm=DRY_MM):
    """NetCDF-ready dataset: max_cdd (days) and first_day_ge_N (int16, -1 = never)."""
    ny, nx = res.max_cdd.shape
    lat = lat0 - d * np.arange(ny)
    lon = lon0 + d * np.arange(nx)
    ds = xr.Dataset(
        {"max_cdd": (("latitude", "longitude"), res.max_cdd, {"units": "days",
                     "long_name": "longest run of consecutive dry days in the forecast"}),
         **{f"first_day_ge_{lv}": (("latitude", "longitude"), f.astype("int16"), {
             "long_name": f"index of the first forecast day on which the dry spell reaches {lv} days "
                          f"(-1 = never)"}) for lv, f in res.first_day.items()}},
        coords={"latitude": ("latitude", lat, {"units": "degrees_north"}),
                "longitude": ("longitude", lon, {"units": "degrees_east"})},
        attrs={"model_run": f"{res.run:%Y-%m-%dT%H:%M}Z",
               "first_forecast_day": res.dates[0].isoformat(),
               "last_forecast_day": res.dates[-1].isoformat(),
               "dry_day_threshold_mm": dry_mm,
               "note": "UTC calendar days; streaks start at forecast day 0, so a dry spell already "
                       "under way at the start is under-counted"})
    return ds
