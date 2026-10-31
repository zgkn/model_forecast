"""Tests for timeseries.py: box cells, box bounds and the box-average series."""
import numpy as np
import pytest

import cams
import timeseries as ts

GRID = cams.Grid(lat_n=3.0, lat_s=0.0, lon_w=103.0, lon_e=105.0, step=0.25)      # 13 x 9 nodes
PLACE = {"name": "Test", "lats": [1.5, 1.25], "lons": [103.75, 104.0]}


def test_cells_and_box():
    assert ts.cells(GRID, PLACE) == ([6, 7], [3, 4])
    assert ts.box(GRID, PLACE) == [[1.125, 103.625], [1.625, 104.125]]


def test_cells_outside_or_off_grid():
    with pytest.raises(ValueError, match="outside"):
        ts.cells(GRID, {"name": "X", "lats": [9.0], "lons": [103.5]})
    with pytest.raises(ValueError, match="not a grid node"):
        ts.cells(GRID, {"name": "X", "lats": [1.4], "lons": [103.5]})


def fields(steps=4):
    ny, nx = GRID.ny, GRID.nx
    u = np.zeros((steps, 3, ny, nx), "float32")
    v = np.zeros_like(u)
    rh = np.zeros((steps, ny, nx), "float32")
    tp = np.zeros_like(rh)
    return u, v, rh, tp


def test_mean_only_over_the_box_cells():
    u, v, rh, tp = fields()
    rh[:] = 10
    rh[:, 6:8, 3:5] = [[40, 60], [80, 100]]                 # inside the box: mean 70
    out = ts.nwp_series(u, v, rh, tp, GRID, [925, 850, 700], PLACE)
    assert out["rh"] == [70] * 4


def test_wind_speed_in_knots_and_direction_from():
    u, v, rh, tp = fields()
    u[:, 0, 6:8, 3:5] = -5.0                                # blowing towards the west = from the east (090)
    v[:, 1, 6:8, 3:5] = -5.0                                # blowing towards the south = from the north (360 -> 0)
    u[:, 2, 6:8, 3:5] = 5.0                                 # towards the east = from the west (270)
    out = ts.nwp_series(u, v, rh, tp, GRID, [925, 850, 700], PLACE)
    assert out["speed"]["925"][0] == pytest.approx(5 * ts.KT, abs=0.05)
    assert out["dir"]["925"][0] == 90
    assert out["dir"]["850"][0] == 0
    assert out["dir"]["700"][0] == 270


def test_rain_is_the_increase_of_the_accumulation_and_never_negative():
    u, v, rh, tp = fields()
    tp[:, 6:8, 3:5] = np.array([0, 2, 2, 5], "float32")[:, None, None]
    tp[3, 6:8, 3:5] = 1.0                                   # accumulation can dip (bad data): rain stays >= 0
    out = ts.nwp_series(u, v, rh, tp, GRID, [925, 850, 700], PLACE)
    assert out["rain"] == [0.0, 2.0, 0.0, 0.0]


def test_aod_series():
    aod = np.zeros((3, GRID.ny, GRID.nx), "float32")
    aod[:, 6:8, 3:5] = np.array([0.5, 1.0, 1.5])[:, None, None]
    aod[:, 0, 0] = 5                                        # outside the box, must not count
    assert ts.aod_series(aod, GRID, PLACE) == {"aod": [0.5, 1.0, 1.5]}


def test_all_places_are_grid_nodes_inside_the_map_and_mostly_land():
    import json
    import cdd
    import wind_forecast as w
    land = cdd.land_mask(json.loads(w.LAND_FILE.read_text()), w.LAT_N, w.LON_W, w.STEP, w.NY, w.NX)
    assert len({p["id"] for p in ts.PLACES}) == len(ts.PLACES)
    for pl in ts.PLACES:
        rows, cols = ts.cells(w.GRID, pl)                   # raises when off the grid / not a node
        assert land[np.ix_(rows, cols)].mean() >= 0.75, f"{pl['name']} is mostly sea"
