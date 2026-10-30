"""Tests for cams.py with synthetic CAMS-like files and a fake cdsapi client (no network)."""
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import cams

GRID = cams.Grid(lat_n=10.0, lat_s=0.0, lon_w=100.0, lon_e=106.0, step=0.5)
CYCLE = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
HOURS = [0, 3, 6, 9]


def aod_field(lats, lons, steps):
    """AOD that is linear in lat, lon and step, so linear interpolation must reproduce it exactly."""
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    return np.stack([0.05 * la + 0.02 * (lo - 100) + 0.1 * k for k in range(steps)]).astype("float32")


def synthetic(layout, path, name="omaod550", lon_shift=0.0):
    lats = np.arange(11.5, -1.6, -0.4)                     # native-like 0.4 degree grid, north to south, with margin
    lons = np.arange(98.4, 107.7, 0.4) + lon_shift
    data = aod_field(lats, lons - lon_shift, len(HOURS))
    ref = np.datetime64("2026-10-30T12:00")
    lead = np.array(HOURS, dtype="timedelta64[h]").astype("timedelta64[ns]")
    valid = ref + lead
    if layout == "period":                                  # reference time + forecast_period
        ds = xr.Dataset({name: (("forecast_period", "latitude", "longitude"), data)},
                        coords={"forecast_period": lead, "latitude": lats, "longitude": lons,
                                "forecast_reference_time": ref})
    elif layout == "valid_dim":                             # one flat valid_time dimension
        ds = xr.Dataset({name: (("valid_time", "latitude", "longitude"), data)},
                        coords={"valid_time": valid, "latitude": lats, "longitude": lons})
    else:                                                   # 2-D valid_time (reference x lead), size-1 reference dim
        ds = xr.Dataset({name: (("forecast_reference_time", "forecast_period", "latitude", "longitude"), data[None])},
                        coords={"forecast_reference_time": [ref], "forecast_period": lead,
                                "valid_time": (("forecast_reference_time", "forecast_period"), valid[None]),
                                "latitude": lats, "longitude": lons})
    ds.to_netcdf(path, engine="h5netcdf")


def test_latest_cycles():
    c = cams.latest_cycles(datetime(2026, 10, 30, 13, 10, tzinfo=timezone.utc), 3)
    assert [f"{x:%d %H}" for x in c] == ["30 12", "30 00", "29 12", "29 00"]
    c = cams.latest_cycles(datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc), 2)
    assert [f"{x:%d %H}" for x in c] == ["30 00", "29 12", "29 00"]


def test_request_matches_the_aod_pm_project():
    r = cams.build_request(CYCLE, GRID)
    assert r["variable"] == ["organic_matter_aerosol_optical_depth_550nm"]
    assert r["date"] == ["2026-10-30/2026-10-30"] and r["time"] == ["12:00"] and r["type"] == ["forecast"]
    assert r["leadtime_hour"][0] == "0" and r["leadtime_hour"][-1] == "120" and len(r["leadtime_hour"]) == 41
    assert r["area"] == [11.0, 99.0, -1.0, 107.0]            # N, W, S, E with a 1 degree margin
    assert r["data_format"] == "netcdf_zip"


@pytest.mark.parametrize("layout", ["period", "valid_dim", "two_d_valid"])
def test_read_forecast_any_time_layout_and_regrid_is_exact(tmp_path, layout):
    p = tmp_path / "f.nc"
    synthetic(layout, p)
    valid, aod = cams.read_forecast(p, GRID)
    assert list(valid) == [pd.Timestamp("2026-10-30 12:00") + pd.Timedelta(hours=h) for h in HOURS]
    assert aod.shape == (4, GRID.ny, GRID.nx)
    lat, lon = GRID.lats()[3], GRID.lons()[5]                # row 3 is north -> south, so lat falls with the row
    assert aod[2, 3, 5] == pytest.approx(0.05 * lat + 0.02 * (lon - 100) + 0.2, abs=1e-4)


def test_long_variable_name_and_0_360_longitudes(tmp_path):
    p = tmp_path / "f.nc"
    synthetic("period", p, name="organic_matter_aerosol_optical_depth_550nm")
    assert cams.read_forecast(p, GRID)[1].shape[0] == 4


def test_quantise_saturates_at_the_top_of_the_scale():
    q = cams.quantise(np.array([-0.3, 0.0, 0.01, 0.1, 2.5, 5.0, 5.1, 9.0]))
    assert q.tolist() == [0, 0, 0, 5, 125, 250, 255, 255]
    assert q.dtype == np.uint8 and 250 * cams.STEP_Q == pytest.approx(5.0)


class Result:
    def __init__(self, writer):
        self.writer = writer

    def download(self, target):
        self.writer(target)


class Client:
    """Fake cdsapi client: `available` maps cycle hour-string -> writer; others raise like an unpublished cycle."""

    def __init__(self, available):
        self.available, self.requests = available, []

    def retrieve(self, dataset, request):
        self.requests.append((dataset, request))
        key = f"{request['date'][0][:10]} {request['time'][0]}"
        if key not in self.available:
            raise RuntimeError("None of the data you have requested is available yet")
        return Result(self.available[key])


def write_zip(tmp_path):
    def writer(target):
        nc = tmp_path / "inner.nc"
        synthetic("period", nc)
        with zipfile.ZipFile(target, "w") as z:
            z.write(nc, "data.nc")
    return writer


def test_fetch_walks_back_to_a_published_cycle_unzips_and_prunes(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "cams_2026102900.npz").write_bytes(b"old")
    client = Client({"2026-10-30 00:00": write_zip(tmp_path)})     # the 12Z cycle is not published yet
    now = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)
    msgs = []
    path = cams.fetch(cache, GRID, now=now, client=client, log=msgs.append)
    assert path.name == "cams_2026103000.npz"
    assert [r[1]["time"][0] for r in client.requests] == ["12:00", "00:00"]
    assert [p.name for p in cache.iterdir()] == ["cams_2026103000.npz"]          # older cache deleted
    c = cams.load_cache(path, GRID)
    assert c["steps"] == [12, 15, 18, 21] or c["steps"] == [0, 3, 6, 9]
    assert c["aod_q"].shape == (4, GRID.ny, GRID.nx) and c["aod_q"].dtype == np.uint8


def test_fetch_steps_are_hours_after_the_cycle(tmp_path):
    cache = tmp_path / "cache"
    client = Client({"2026-10-30 12:00": write_zip(tmp_path)})
    path = cams.fetch(cache, GRID, now=datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc), client=client, log=lambda m: None)
    assert cams.load_cache(path, GRID)["steps"] == [0, 3, 6, 9]
    assert cams.run_of(path) == CYCLE


def test_cached_cycle_needs_no_download(tmp_path):
    cache = tmp_path / "cache"
    first = Client({"2026-10-30 12:00": write_zip(tmp_path)})
    cams.fetch(cache, GRID, now=datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc), client=first, log=lambda m: None)
    second = Client({})
    path = cams.fetch(cache, GRID, now=datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc), client=second, log=lambda m: None)
    assert path.name == "cams_2026103012.npz" and second.requests == []


def test_nothing_available_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="no CAMS cycle available"):
        cams.fetch(tmp_path / "c", GRID, now=CYCLE, client=Client({}), max_back=1, log=lambda m: None)


def test_cache_for_another_grid_is_not_used(tmp_path):
    p = tmp_path / "cams_2026103012.npz"
    cams.save_cache(p, [0, 3], np.zeros((2, GRID.ny, GRID.nx), "uint8"), GRID)
    assert cams.load_cache(p, GRID) is not None
    assert cams.load_cache(p, cams.Grid(10.0, 0.0, 100.0, 107.0, 0.5)) is None
