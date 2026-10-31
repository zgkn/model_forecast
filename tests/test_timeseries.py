"""Tests for timeseries.py: box cells, cell coverage of outlines and the mean / max series."""
import json

import numpy as np
import pytest

import cams
import timeseries as ts

GRID = cams.Grid(lat_n=3.0, lat_s=0.0, lon_w=103.0, lon_e=105.0, step=0.25)      # 13 x 9 nodes
PLACE = {"name": "Test", "south": 1.125, "north": 1.625, "west": 103.625, "east": 104.125}


def box_place(**kw):
    p = dict(PLACE, **kw)
    return {"id": "t", "name": p["name"], "kind": "box", "w": ts.box_weights(GRID, p)}


def test_cells_and_box():
    assert ts.cells(GRID, PLACE) == ([6, 7], [3, 4])
    assert ts.box(GRID, PLACE) == [[1.125, 103.625], [1.625, 104.125]]


def test_cells_include_the_nodes_on_the_edges():
    place = {"name": "Wide", "south": 1.0, "north": 2.0, "west": 103.0, "east": 104.0}
    rows, cols = ts.cells(GRID, place)
    assert rows == [4, 5, 6, 7, 8] and cols == [0, 1, 2, 3, 4]        # 2.0 .. 1.0 N and 103.0 .. 104.0 E, both edges in


def test_cells_outside_or_empty():
    with pytest.raises(ValueError, match="outside"):
        ts.cells(GRID, {"name": "X", "south": 8.0, "north": 9.0, "west": 103.5, "east": 104.0})
    with pytest.raises(ValueError, match="no grid node"):
        ts.cells(GRID, {"name": "X", "south": 1.30, "north": 1.40, "west": 103.5, "east": 104.0})


def test_region_weights_are_the_covered_fraction_of_each_cell():
    # cells are 0.25 squares around the nodes: the cell of node (1.5, 103.75) spans 1.375-1.625 N, 103.625-103.875 E
    square = {"type": "Polygon", "coordinates": [[[103.625, 1.375], [103.875, 1.375], [103.875, 1.625], [103.625, 1.625], [103.625, 1.375]]]}
    w = ts.region_weights(square, GRID)
    assert w[6, 3] == pytest.approx(1.0)                                # exactly that cell
    assert w.sum() == pytest.approx(1.0)
    half = {"type": "Polygon", "coordinates": [[[103.625, 1.375], [103.75, 1.375], [103.75, 1.625], [103.625, 1.625], [103.625, 1.375]]]}
    assert ts.region_weights(half, GRID)[6, 3] == pytest.approx(0.5)    # half of the cell


def test_region_weights_respect_holes():
    outer = [[103.0, 0.0], [105.0, 0.0], [105.0, 3.0], [103.0, 3.0], [103.0, 0.0]]
    hole = [[103.875, 1.375], [104.125, 1.375], [104.125, 1.625], [103.875, 1.625], [103.875, 1.375]]   # one cell
    full = ts.region_weights({"type": "Polygon", "coordinates": [outer]}, GRID)
    holed = ts.region_weights({"type": "Polygon", "coordinates": [outer, hole]}, GRID)
    assert full.sum() - holed.sum() == pytest.approx(1.0, abs=0.05)


def fields(steps=4):
    ny, nx = GRID.ny, GRID.nx
    u = np.zeros((steps, 3, ny, nx), "float32")
    v = np.zeros_like(u)
    rh = np.zeros((steps, ny, nx), "float32")
    tp = np.zeros_like(rh)
    return u, v, rh, tp


def series(u, v, rh, tp, place=None):
    return ts.nwp_series(u, v, rh, tp, GRID, [925, 850, 700], place or box_place())


def test_mean_only_over_the_box_cells_and_max_is_the_largest_cell():
    u, v, rh, tp = fields()
    rh[:] = 10
    rh[:, 6:8, 3:5] = [[40, 60], [80, 100]]                 # inside the box: mean 70, max 100
    out = series(u, v, rh, tp)
    assert out["mean"]["rh"] == [70] * 4
    assert out["max"]["rh"] == [100] * 4


def test_wind_speed_in_knots_and_direction_from():
    u, v, rh, tp = fields()
    u[:, 0, 6:8, 3:5] = -5.0                                # blowing towards the west = from the east (090)
    v[:, 1, 6:8, 3:5] = -5.0                                # blowing towards the south = from the north (360 -> 0)
    u[:, 2, 6:8, 3:5] = 5.0                                 # towards the east = from the west (270)
    out = series(u, v, rh, tp)
    assert out["mean"]["speed"]["925"][0] == pytest.approx(5 * ts.KT, abs=0.05)
    assert out["max"]["speed"]["925"][0] == pytest.approx(5 * ts.KT, abs=0.05)
    assert [out["mean"]["dir"][l][0] for l in ("925", "850", "700")] == [90, 0, 270]
    assert "dir" not in out["max"]                          # no maximum of directions


def test_rain_is_the_increase_of_the_accumulation_and_never_negative():
    u, v, rh, tp = fields()
    tp[:, 6:8, 3:5] = np.array([0, 2, 2, 5], "float32")[:, None, None]
    tp[3, 6:8, 3:5] = 1.0                                   # accumulation can dip (bad data): rain stays >= 0
    out = series(u, v, rh, tp)
    assert out["mean"]["rain"] == [0.0, 2.0, 0.0, 0.0]
    assert out["mean"]["cum"] == [0.0, 2.0, 2.0, 2.0]


def test_cumulative_max_is_the_cell_with_the_most_rain_not_the_sum_of_maxima():
    u, v, rh, tp = fields()
    tp[:, 6, 3] = [0, 4, 4, 4]                              # cell A rains early: total 4
    tp[:, 7, 4] = [0, 0, 0, 6]                              # cell B rains late: total 6
    out = series(u, v, rh, tp)
    assert out["max"]["rain"] == [0.0, 4.0, 0.0, 6.0]
    assert out["max"]["cum"] == [0.0, 4.0, 4.0, 10.0 - 4.0]  # not 4 + 6: the cells are different
    assert out["mean"]["cum"][-1] == pytest.approx(2.5)


def test_region_mean_is_weighted_and_max_ignores_barely_covered_cells():
    u, v, rh, tp = fields()
    w = np.zeros((GRID.ny, GRID.nx), "float32")
    w[6, 3], w[6, 4], w[7, 3] = 1.0, 0.5, 0.1               # a full cell, a half cell, a sliver
    rh[:, 6, 3], rh[:, 6, 4], rh[:, 7, 3] = 20, 60, 100
    out = series(u, v, rh, tp, {"id": "r", "name": "R", "kind": "region", "w": w})
    assert out["mean"]["rh"] == [round((20 * 1.0 + 60 * 0.5 + 100 * 0.1) / 1.6)] * 4
    assert out["max"]["rh"] == [60] * 4                     # the 10 % cell does not count


def test_max_falls_back_to_the_best_covered_cell_for_a_tiny_region():
    u, v, rh, tp = fields()
    w = np.zeros((GRID.ny, GRID.nx), "float32")
    w[6, 3], w[6, 4] = 0.3, 0.2
    rh[:, 6, 3], rh[:, 6, 4] = 40, 90
    out = series(u, v, rh, tp, {"id": "r", "name": "R", "kind": "region", "w": w})
    assert out["max"]["rh"] == [40] * 4


def test_aod_series():
    aod = np.zeros((3, GRID.ny, GRID.nx), "float32")
    aod[:, 6:8, 3:5] = np.array([0.5, 1.0, 1.5])[:, None, None]
    aod[:, 6, 3] = np.array([2.0, 2.0, 3.0])                # one hot cell
    aod[:, 0, 0] = 5                                        # outside the place, must not count
    out = ts.aod_series(aod, GRID, box_place())
    assert out["max"] == {"aod": [2.0, 2.0, 3.0]}
    assert out["mean"]["aod"] == [pytest.approx(0.88, abs=0.01), pytest.approx(1.25, abs=0.01), pytest.approx(1.88, abs=0.01)]


def test_the_shipped_places_are_valid_on_the_real_grid():
    import wind_forecast as w
    places = ts.load_places(w.GRID)
    ids = [p["id"] for p in places]
    assert len(set(ids)) == len(ids) and "sg" in ids
    assert {"riau", "jambi", "ssum", "wkal", "ckal", "skal", "khm", "ktinggi", "mersing"} <= set(ids)
    for p in places:
        assert p["w"].shape == (w.NY, w.NX) and p["w"].sum() >= 1.0, p["name"]
        s, west = p["box"][0]
        n, east = p["box"][1]
        assert w.LAT_S <= s < n <= w.LAT_N and w.LON_W <= west < east <= w.LON_E, p["name"]
    sizes = {d["id"]: d["cells"] for d in ts.describe(places)}
    assert 100 < sizes["riau"] < 1000 and sizes["sg"] == 4.0
    assert 200 < sizes["khm"] < 300                          # Cambodia, about 181,000 km2 of 28 x 28 km cells
    groups = {d["id"]: d["group"] for d in ts.describe(places)}
    assert groups["khm"] == "Countries" and groups["ktinggi"] == groups["mersing"] == "Districts"
    assert 3 < sizes["ktinggi"] < 6 and 3 < sizes["mersing"] < 6     # districts of 3000-3500 km2: a few 28 x 28 km cells
    json.dumps(ts.describe(places))                         # serialisable for the manifest
