"""Synthetic-data tests for cdd.py (no network, no GRIB)."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

import cdd


def dry_array(pattern):
    """(days, 1, 1) boolean array from a 1/0 pattern (1 = dry)."""
    return np.array(pattern, dtype=bool).reshape(-1, 1, 1)


def test_longest_spell_and_first_day():
    run, first = cdd.streaks(dry_array([1, 1, 1, 1, 1, 0, 1, 1, 1]))
    assert run[-1, 0, 0] == 5
    assert first[5][0, 0] == 4
    assert first[10][0, 0] == -1


def test_max_is_longest_not_last():
    run, _ = cdd.streaks(dry_array([0, 1, 1, 1, 0, 1]))
    assert run[-1, 0, 0] == 3
    assert list(run[:, 0, 0]) == [0, 1, 2, 3, 3, 3]   # running maximum "so far"


def test_all_dry_fifteen_days():
    _, first = cdd.streaks(dry_array([1] * 15))
    assert (first[5][0, 0], first[10][0, 0], first[15][0, 0]) == (4, 9, 14)


def synthetic_tp(run_hour, step_list, daily_mm=1.0, shape=(2, 2)):
    """Accumulated tp in metres that grows by daily_mm per 24 h, plus the run time."""
    run = pd.Timestamp(2026, 10, 26, run_hour)
    tp = np.array([np.full(shape, s / 24 * daily_mm / 1000) for s in step_list])
    return run, tp


def test_daily_rain_00z_gives_15_days():
    steps = list(range(0, 361, 6))
    run, tp = synthetic_tp(0, steps)
    dates, rain = cdd.daily_rain(run, steps, tp)
    assert len(dates) == 15 and rain.shape[0] == 15
    assert dates[0] == dt.date(2026, 10, 26) and dates[-1] == dt.date(2026, 11, 9)
    assert np.allclose(rain, 1.0, atol=1e-4)


def test_daily_rain_12z_gives_14_days_and_first_date_is_a_midnight():
    steps = list(range(0, 361, 6))
    run, tp = synthetic_tp(12, steps)
    dates, rain = cdd.daily_rain(run, steps, tp)
    assert len(dates) == 14
    assert dates[0] == dt.date(2026, 10, 27)            # first midnight is +12 h = 27 Oct 00 UTC
    assert dates[-1] == dt.date(2026, 11, 9)


def test_rain_lands_on_the_right_date():
    steps = list(range(0, 361, 6))
    run = pd.Timestamp(2026, 10, 26, 0)
    tp = np.zeros((len(steps), 1, 1))
    tp[steps.index(60):] += 0.010                        # 10 mm falls between +54 h and +60 h = 28 Oct
    dates, rain = cdd.daily_rain(run, steps, tp)
    wet = [d for d, r in zip(dates, rain[:, 0, 0]) if r > 1]
    assert wet == [dt.date(2026, 10, 28)]


def test_negative_packing_noise_is_clipped_and_gaps_are_nan():
    steps = [0, 24, 48, 96, 120, 144]                    # the 72 h midnight is missing
    run = pd.Timestamp(2026, 10, 26, 0)
    tp = np.array([0, 0.002, 0.0019999, 0.004, 0.005, 0.006]).reshape(-1, 1, 1)
    dates, rain = cdd.daily_rain(run, steps, tp)
    assert rain[1, 0, 0] == 0                             # tiny negative clipped to 0
    assert np.isnan(rain[2, 0, 0])                        # 48 h -> 96 h is not a single day
    assert np.isnan(rain[2, 0, 0]) and not np.isnan(rain[3, 0, 0])


def test_too_few_days_is_an_error():
    steps = list(range(0, 97, 6))                         # 4 days only (a 06/18Z-style short run)
    run, tp = synthetic_tp(0, steps)
    with pytest.raises(ValueError):
        cdd.daily_rain(run, steps, tp)


def test_nan_is_not_dry():
    run, _ = cdd.streaks(np.array([np.nan, 0.2, 0.2]).reshape(-1, 1, 1) < 1.0)
    assert run[-1, 0, 0] == 2                             # NaN breaks the streak instead of extending it


def grid_result(max_cdd, level_first=None, d=0.25, lat0=10.0, lon0=100.0):
    max_cdd = np.asarray(max_cdd, dtype="float32")
    dates = [dt.date(2026, 10, 26) + dt.timedelta(days=i) for i in range(15)]
    first = {lv: np.where(np.nan_to_num(max_cdd, nan=-1) >= lv, lv - 1, -1).astype("int16") for lv in cdd.LEVELS}
    if level_first:
        first.update(level_first)
    return cdd.threshold_regions(max_cdd, first, dates, lat0, lon0, d), dates


def bbox(feature):
    pts = [p for ring in feature["geometry"]["coordinates"] for p in ring]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def test_block_of_exactly_five_is_one_region_with_the_right_extent_and_date():
    m = np.zeros((6, 6), dtype="float32")
    m[2:4, 2:4] = 5
    fc, dates = grid_result(m)
    feats = [f for f in fc["features"] if f["properties"]["level"] == 5]
    assert len(feats) == 1 and not [f for f in fc["features"] if f["properties"]["level"] == 10]
    x0, y0, x1, y1 = bbox(feats[0])
    assert (x1 - x0, y1 - y0) == (0.5, 0.5)
    assert feats[0]["properties"]["earliest"] == dates[4].isoformat()
    assert feats[0]["properties"]["peak"] == "5 days"


def test_region_with_a_hole_keeps_its_interior_ring():
    m = np.full((7, 7), 6, dtype="float32")
    m[3, 3] = 0
    fc, _ = grid_result(m)
    (feat,) = [f for f in fc["features"] if f["properties"]["level"] == 5]
    assert len(feat["geometry"]["coordinates"]) == 2      # outer ring + hole
    ring = feat["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]                            # closed


def test_two_blobs_get_their_own_dates_and_numbers():
    m = np.zeros((5, 12), dtype="float32")
    m[1:3, 1:3] = 5
    m[1:3, 8:11] = 7
    first5 = np.full((5, 12), -1, dtype="int16")
    first5[1:3, 1:3] = 4
    first5[1:3, 8:11] = [[6, 6, 7], [6, 6, 7]]
    fc, dates = grid_result(m, {5: first5})
    feats = sorted((f for f in fc["features"] if f["properties"]["level"] == 5), key=lambda f: bbox(f)[0])
    assert [f["properties"]["earliest"] for f in feats] == [dates[4].isoformat(), dates[6].isoformat()]
    assert [f["properties"]["peak"] for f in feats] == ["5 days", "7 days"]


def test_diagonal_cells_are_not_connected():
    m = np.zeros((4, 4), dtype="float32")
    m[1, 1] = m[2, 2] = 5
    fc, _ = grid_result(m)
    assert len([f for f in fc["features"] if f["properties"]["level"] == 5]) == 2   # 4-connectivity


def test_nan_cells_never_appear_in_a_region():
    m = np.full((4, 4), np.nan, dtype="float32")
    m[1:3, 1:3] = 5
    fc, _ = grid_result(m)
    (feat,) = [f for f in fc["features"] if f["properties"]["level"] == 5]
    x0, y0, x1, y1 = bbox(feat)
    assert (x1 - x0, y1 - y0) == (0.5, 0.5)


def test_area_uses_the_cells_of_the_component_only():
    m = np.zeros((6, 6), dtype="float32")
    m[2:4, 2:4] = 5
    fc, _ = grid_result(m, lat0=0.0)
    km2 = int(fc["features"][0]["properties"]["area"].split()[0])
    assert km2 == pytest.approx(4 * (0.25 * 111.32) ** 2, rel=0.01)   # near the equator cos(lat) ~ 1


def test_land_mask_keeps_a_small_island_that_point_sampling_would_miss():
    # ~23% of the cell at (10N, 100E), but the cell centre itself is open water
    island = {"type": "Polygon", "coordinates": [[[100.01, 9.90], [100.12, 9.90], [100.12, 10.02],
                                                    [100.01, 10.02], [100.01, 9.90]]]}
    mask = cdd.land_mask(island, lat0=10.0, lon0=100.0, d=0.25, ny=4, nx=4)
    assert mask.sum() == 1 and mask[0, 0]
    tiny = {"type": "Polygon", "coordinates": [[[100.01, 9.90], [100.08, 9.90], [100.08, 9.97],
                                                  [100.01, 9.97], [100.01, 9.90]]]}   # ~8% of the cell
    assert not cdd.land_mask(tiny, lat0=10.0, lon0=100.0, d=0.25, ny=4, nx=4).any()


def test_all_ocean_grid_raises():
    steps = list(range(0, 361, 24))
    run, tp = synthetic_tp(0, steps)
    with pytest.raises(ValueError):
        cdd.compute(run, steps, tp, np.zeros((2, 2), bool), 10.0, 100.0, 0.25)


def test_compute_masks_ocean_and_reports_dataset():
    steps = list(range(0, 361, 24))
    run, tp = synthetic_tp(0, steps, daily_mm=0.0, shape=(2, 2))
    land = np.array([[True, False], [True, True]])
    res = cdd.compute(run, steps, tp, land, 10.0, 100.0, 0.25)
    assert np.isnan(res.max_cdd[0, 1]) and res.max_cdd[0, 0] == 15
    assert res.first_day[10][0, 1] == -1 and res.first_day[10][0, 0] == 9
    assert res.running_max[:, 0, 1].max() == -1
    ds = cdd.to_dataset(res, 10.0, 100.0, 0.25)
    assert ds.attrs["dry_day_threshold_mm"] == 1.0 and ds.attrs["first_forecast_day"] == "2026-10-26"
    assert ds["first_day_ge_5"].dtype == "int16"
