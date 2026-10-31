"""Box-average time series for the place box on the map (Singapore).

The box is a block of whole grid cells, so its average is exactly the mean of those grid nodes (no
interpolation). Everything is returned as plain rounded lists that go straight into the page manifest.
"""
import numpy as np

KT = 1.943844                                   # m/s -> knots
PLACE = {"name": "Singapore", "lats": [1.5, 1.25], "lons": [103.75, 104.0]}   # grid nodes of the box (2 x 2 cells)


def cells(grid, place=PLACE):
    """(rows, cols) of the box's grid nodes in the map grid, north to south and west to east."""
    rows = [round((grid.lat_n - lat) / grid.step) for lat in place["lats"]]
    cols = [round((lon - grid.lon_w) / grid.step) for lon in place["lons"]]
    if min(rows) < 0 or max(rows) >= grid.ny or min(cols) < 0 or max(cols) >= grid.nx:
        raise ValueError(f"{place['name']} is outside the map grid")
    for r, lat in zip(rows, place["lats"]):
        if abs(grid.lat_n - r * grid.step - lat) > 1e-6:
            raise ValueError(f"latitude {lat} is not a grid node")
    for c, lon in zip(cols, place["lons"]):
        if abs(grid.lon_w + c * grid.step - lon) > 1e-6:
            raise ValueError(f"longitude {lon} is not a grid node")
    return rows, cols


def box(grid, place=PLACE):
    """[[south, west], [north, east]]: the outer edges of the cells (half a step beyond the nodes)."""
    h = grid.step / 2
    return [[min(place["lats"]) - h, min(place["lons"]) - h], [max(place["lats"]) + h, max(place["lons"]) + h]]


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
