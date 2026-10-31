"""Area time series for the boxes and provinces on the map.

A place is a weight per grid cell: 1 on the nodes inside a box (edges included), or the fraction of the cell that lies
inside a province outline (cells are the 0.25 degree squares around the nodes). The series are the weighted mean of the
cells, and the maximum over the cells that are at least half inside the place (the best covered cell when none is).
Everything is returned as plain rounded lists that go straight into the page manifest.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

KT = 1.943844                                   # m/s -> knots
EPS = 1e-6
OVERSAMPLE = 10                                 # sub-cells per cell side when measuring how much of a cell is inside an outline
MAX_COVERAGE = 0.5                              # a cell counts for the maximum when at least this much of it is inside
REGIONS_FILE = Path(__file__).resolve().parent / "regions.geojson"

# boxes (edges in degrees); Singapore is the 2 x 2 grid cells around the island
BOXES = [{"id": "sg", "name": "Singapore", "south": 1.125, "north": 1.625, "west": 103.625, "east": 104.125}]
PLACE = BOXES[0]


def cells(grid, place=PLACE):
    """(rows, cols): indices of the map grid nodes inside the box, north to south and west to east."""
    if (place["south"] < grid.lat_s - EPS or place["north"] > grid.lat_n + EPS
            or place["west"] < grid.lon_w - EPS or place["east"] > grid.lon_e + EPS):
        raise ValueError(f"{place['name']} is outside the map grid")
    rows = [i for i, lat in enumerate(grid.lats()) if place["south"] - EPS <= lat <= place["north"] + EPS]
    cols = [j for j, lon in enumerate(grid.lons()) if place["west"] - EPS <= lon <= place["east"] + EPS]
    if not rows or not cols:
        raise ValueError(f"{place['name']} contains no grid node")
    return rows, cols


def box(grid, place=PLACE):
    """[[south, west], [north, east]] as drawn on the map."""
    return [[place["south"], place["west"]], [place["north"], place["east"]]]


def box_weights(grid, place=PLACE):
    w = np.zeros((grid.ny, grid.nx), "float32")
    rows, cols = cells(grid, place)
    w[np.ix_(rows, cols)] = 1
    return w


def region_weights(geometry, grid, oversample=OVERSAMPLE):
    """(ny, nx) fraction of every grid cell that lies inside the outline (a GeoJSON Polygon/MultiPolygon)."""
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    sub = grid.step / oversample
    shape = (grid.ny * oversample, grid.nx * oversample)
    transform = from_origin(grid.lon_w - grid.step / 2, grid.lat_n + grid.step / 2, sub, sub)
    fine = rasterize([(geometry, 1)], out_shape=shape, transform=transform, fill=0, dtype="uint8")
    return fine.reshape(grid.ny, oversample, grid.nx, oversample).mean(axis=(1, 3)).astype("float32")


def _bounds(geometry):
    pts = np.array([p for poly in ([geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"])
                    for ring in poly for p in ring])
    return [[float(pts[:, 1].min()), float(pts[:, 0].min())], [float(pts[:, 1].max()), float(pts[:, 0].max())]]


def region_places(grid, path=REGIONS_FILE):
    out = []
    if not Path(path).exists():
        return out
    for f in json.loads(Path(path).read_text())["features"]:
        w = region_weights(f["geometry"], grid)
        if w.sum() < EPS:
            raise ValueError(f"{f['properties']['name']} covers no cell of the map grid")
        out.append({"id": f["properties"]["id"], "name": f["properties"]["name"], "kind": "region", "w": w,
                    "box": _bounds(f["geometry"])})
    return out


@lru_cache(maxsize=None)
def load_places(grid):
    """All places of the page: the provinces first, then the boxes; each has id, name, kind, w (weights) and box."""
    places = region_places(grid)
    for b in BOXES:
        places.append({"id": b["id"], "name": b["name"], "kind": "box", "w": box_weights(grid, b), "box": box(grid, b)})
    return places


def describe(places):
    """The part of each place the viewer needs: id, name, kind, outline box and the number of cells it covers."""
    return [{"id": p["id"], "name": p["name"], "kind": p["kind"], "box": p["box"], "cells": round(float(p["w"].sum()), 1)}
            for p in places]


class _Area:
    """Weighted mean and masked maximum over the last two axes (lat, lon)."""

    def __init__(self, w):
        self.w = np.asarray(w, "float64")
        self.total = self.w.sum()
        mask = self.w >= MAX_COVERAGE
        self.mask = mask if mask.any() else self.w == self.w.max()

    def mean(self, a):
        return np.tensordot(a, self.w, axes=([-2, -1], [-2, -1])) / self.total

    def max(self, a):
        return a[..., self.mask].max(axis=-1)


def nwp_series(u, v, rh, tp, grid, levels, place):
    """Series of an IFS/AIFS cache: u, v (steps, levels, ny, nx) in m/s, rh (steps, ny, nx) in %, tp accumulated mm.

    Returns {"mean": {...}, "max": {...}}. speed: kt per level (mean of the cells' speeds); dir: where the mean wind
    blows from (mean only, a maximum of directions means nothing); rain: mm in the preceding model interval; cum: rain
    total since the first step; rh: %.
    """
    area = _Area(place["w"])
    speed = np.hypot(u, v) * KT
    rain = np.clip(np.diff(tp, axis=0, prepend=tp[:1]), 0, None)        # per cell, as in the rain file
    cum = np.cumsum(rain, axis=0)
    um, vm = area.mean(u), area.mean(v)
    dirs = (270 - np.degrees(np.arctan2(vm, um))) % 360                  # meteorological: where the wind blows from

    def per_level(a):
        return {str(l): np.round(a[:, i], 1).tolist() for i, l in enumerate(levels)}

    out = {}
    for name, f in (("mean", area.mean), ("max", area.max)):
        out[name] = {"speed": per_level(f(speed)), "rain": np.round(f(rain), 2).tolist(),
                     "cum": np.round(f(cum), 1).tolist(), "rh": np.round(f(rh)).astype(int).tolist()}
    out["mean"]["dir"] = {str(l): (np.round(dirs[:, i]).astype(int) % 360).tolist() for i, l in enumerate(levels)}
    return out


def aod_series(aod, grid, place):
    """CAMS organic matter AOD (steps, ny, nx) -> mean and max per step."""
    area = _Area(place["w"])
    return {"mean": {"aod": np.round(area.mean(aod), 2).tolist()}, "max": {"aod": np.round(area.max(aod), 2).tolist()}}
