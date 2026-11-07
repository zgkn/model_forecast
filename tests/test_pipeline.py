"""A failed ECMWF download must not stop the site being built from the cached run."""
import numpy as np
import pytest

import wind_forecast as wf


def make_cache(tmp_path, model="ifs", stamp="2026102412"):
    ny, nx = wf.NY, wf.NX
    z = np.zeros((1, len(wf.WIND_KEYS), ny, nx), "float32")
    path = tmp_path / f"{model}_{stamp}.npz"
    wf.save_cache(path, [0], z, z, np.zeros((1, ny, nx), "float32"), np.zeros((1, ny, nx), "float32"), 144)
    return path


def boom(*a, **k):
    raise RuntimeError("503 Slow Down")


def test_failed_refresh_uses_the_cached_run(tmp_path, monkeypatch, capsys):
    path = make_cache(tmp_path)
    monkeypatch.setattr(wf, "fetch_model", boom)
    assert wf.refresh_or_cached("ifs", "aws", 144, tmp_path, 4) == path
    assert "using the cached run 2026102412" in capsys.readouterr().out


def test_failed_refresh_without_cache_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "fetch_model", boom)
    with pytest.raises(RuntimeError):
        wf.refresh_or_cached("ifs", "aws", 144, tmp_path, 4)


def test_successful_refresh_is_returned_unchanged(tmp_path, monkeypatch):
    new = make_cache(tmp_path, stamp="2026102600")
    monkeypatch.setattr(wf, "fetch_model", lambda *a, **k: new)
    assert wf.refresh_or_cached("ifs", "aws", 144, tmp_path, 4) == new


# ---- a run picked before its later steps are published is completed by the next refresh -----------------------

from datetime import datetime, timezone


def fake_download(monkeypatch, run_hour, published_up_to, calls):
    run = datetime(2026, 11, 7, run_hour, tzinfo=timezone.utc)
    monkeypatch.setattr(wf, "latest_run", lambda model, source, step: (object(), run))

    def fetch_step(client, r, step, tmp, which=("uv", "qt", "tp")):
        calls.append(step)
        return {"step": step} if step <= published_up_to[0] else None

    def read_step(files):
        z = np.full((len(wf.WIND_KEYS), 2, 2), float(files["step"]), "float32")
        return z, z, np.zeros((2, 2), "float32"), np.zeros((2, 2), "float32")

    monkeypatch.setattr(wf, "fetch_step", fetch_step)
    monkeypatch.setattr(wf, "read_step", read_step)
    return run


def test_late_steps_are_fetched_by_the_next_refresh(tmp_path, monkeypatch, capsys):
    calls, published = [], [150]
    fake_download(monkeypatch, 0, published, calls)
    path = wf.fetch_model("ifs", "google", 240, tmp_path, 1)
    z = np.load(path)
    assert z["steps"].tolist()[-1] == 150 and int(z["max_step"]) == 150          # cached as far as it goes
    assert "only steps up to +150 h are published so far" in capsys.readouterr().out

    calls.clear()
    published[0] = 360                                                          # the rest appears
    wf.fetch_model("ifs", "google", 240, tmp_path, 1)
    assert calls, "the incomplete run must be downloaded again"
    z = np.load(path)
    assert z["steps"].tolist()[-1] == 240 and int(z["max_step"]) == 240

    calls.clear()
    wf.fetch_model("ifs", "google", 240, tmp_path, 1)
    assert not calls, "a complete run is not downloaded again"


def test_a_06z_run_stops_at_144_and_is_not_refetched(tmp_path, monkeypatch):
    calls = []
    fake_download(monkeypatch, 6, [360], calls)
    path = wf.fetch_model("ifs", "google", 240, tmp_path, 1)
    assert np.load(path)["steps"].tolist()[-1] == 144                            # 06/18 UTC IFS runs end at +144 h
    calls.clear()
    wf.fetch_model("ifs", "google", 240, tmp_path, 1)
    assert not calls


def test_a_cache_written_for_144_h_is_extended_for_a_00z_run(tmp_path, monkeypatch):
    calls = []
    run = fake_download(monkeypatch, 0, [360], calls)
    z = np.zeros((1, len(wf.WIND_KEYS), 2, 2), "float32")
    old = wf.cache_file(tmp_path, "ifs", run)
    wf.save_cache(old, [0], z, z, np.zeros((1, 2, 2), "float32"), np.zeros((1, 2, 2), "float32"), 144)
    wf.fetch_model("ifs", "google", 240, tmp_path, 1)
    assert calls and np.load(old)["steps"].tolist()[-1] == 240


# ---- the 10 m wind is one more wind "level" ----------------------------------------------------------------------

def test_wind_levels_end_with_the_surface_wind():
    assert wf.WIND_KEYS == wf.LEVELS + [wf.SURFACE] and wf.SURFACE == "sfc"


def test_an_older_cache_without_the_surface_wind_is_not_used(tmp_path):
    path = make_cache(tmp_path)
    z = dict(np.load(path))
    del z["nwind"]                                           # what caches written before the 10 m wind look like
    old = tmp_path / "ifs_2026102400.npz"
    np.savez_compressed(old, **z)
    assert wf.load_cache(path) is not None and wf.load_cache(old) is None


def test_site_files_include_the_surface_wind(tmp_path):
    ny, nx, n = wf.NY, wf.NX, 3
    rng = np.random.default_rng(1)
    u = rng.normal(size=(n, len(wf.WIND_KEYS), ny, nx)).astype("float32")
    path = tmp_path / "ifs_2026110700.npz"
    wf.save_cache(path, [0, 3, 6], u, u, np.full((n, ny, nx), 50, "float32"), np.zeros((n, ny, nx), "float32"), 6)
    data = tmp_path / "site" / "data"
    data.mkdir(parents=True)
    entry = wf.write_model("ifs", path, 240, data)
    assert set(entry["files"]) >= {"uv925", "uv850", "uv700", "uvsfc"}
    assert (data / "ifs_2026110700_uvsfc.bin.gz").exists()
    assert set(entry["sg"]["sg"]["mean"]["speed"]) == {"925", "850", "700", "sfc"}
