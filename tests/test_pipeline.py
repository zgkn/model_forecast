"""A failed ECMWF download must not stop the site being built from the cached run."""
import numpy as np
import pytest

import wind_forecast as wf


def make_cache(tmp_path, model="ifs", stamp="2026102412"):
    ny, nx = wf.NY, wf.NX
    z = np.zeros((1, len(wf.LEVELS), ny, nx), "float32")
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
