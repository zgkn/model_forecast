"""Box-average time series for the boxes on the map.

A place is a rectangle given by its edges. Its average is the plain mean of the grid nodes inside it (edges included),
so the 0.25 degree grid is used as is, with no interpolation. Everything is returned as plain rounded lists that go
straight into the page manifest.
"""
import numpy as np

KT = 1.943844                                   # m/s -> knots
EPS = 1e-6


def _place(id_, name, south, north, west, east):
    return {"id": id_, "name": name, "south": south, "north": north, "west": west, "east": east}


# the time-series boxes (edges in degrees; Singapore is the 2 x 2 grid cells around the island)
PLACES = [
    _place("sg", "Singapore", 1.125, 1.625, 103.625, 104.125),
    _place("sum", "Sumatra box (95-106E, 5S-0)", -5.0, 0.0, 95.0, 106.0),
    _place("kal", "Kalimantan box (109-115E, 4S-0)", -4.0, 0.0, 109.0, 115.0),
]
PLACE = PLACES[0]


def cells(grid, place=PLACE):
    """(rows, cols): indices of the map grid nodes inside the place's box, north to south and west to east."""
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


def _mean(a, rows, cols):
    """Mean over the box of an array whose last two axes are (lat, lon)."""
    sel = a[..., rows, :][..., cols]
    return sel.mean(axis=(-2, -1))


def nwp_series(u, v, rh, tp, grid, levels, place=PLACE):
    """Series of an IFS/AIFS cache: u, v (steps, levels, ny, nx) in m/s, rh (steps, ny, nx) in %, tp accumulated mm."""
    rows, cols = cells(grid, place)
    us, vs = u[:, :, rows][..., cols], v[:, :, rows][..., cols]
    speed = (np.hypot(us, vs) * KT).mean(axis=(-2, -1))                        # mean of the cells' speeds, knots
    um, vm = us.mean(axis=(-2, -1)), vs.mean(axis=(-2, -1))
    dirs = (270 - np.degrees(np.arctan2(vm, um))) % 360                         # meteorological: where the wind blows from
    rain = np.clip(np.diff(_mean(tp, rows, cols), prepend=_mean(tp, rows, cols)[:1]), 0, None)   # mm in the preceding interval
    return {
        "speed": {str(l): speed[:, i].round(1).tolist() for i, l in enumerate(levels)},
        "dir": {str(l): (np.round(dirs[:, i]).astype(int) % 360).tolist() for i, l in enumerate(levels)},
        "rain": rain.round(2).tolist(),
        "rh": _mean(rh, rows, cols).round(0).astype(int).tolist(),
    }


def aod_series(aod, grid, place=PLACE):
    """CAMS organic matter AOD (steps, ny, nx) -> box mean per step."""
    rows, cols = cells(grid, place)
    return {"aod": _mean(aod, rows, cols).round(2).tolist()}
