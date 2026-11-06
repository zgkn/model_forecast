"""The colour scales the page draws from: consistent lengths and ticks that sit on class edges."""
import pytest

import wind_forecast as w


@pytest.mark.parametrize("name", list(w.PALETTES))
def test_palette_is_consistent(name):
    p = w.PALETTES[name]
    assert len(p["colors"]) == len(p["edges"]) + 1 == len(p["alpha"])
    assert p["edges"] == sorted(p["edges"]) and len(set(p["edges"])) == len(p["edges"])
    assert set(p.get("ticks", p["edges"])) <= set(p["edges"])
    assert all(c.startswith("#") and len(c) == 7 for c in p["colors"])


def test_accumulated_rain_scale_reaches_300_mm():
    p = w.PALETTES["accum"]
    assert p["unit"] == "mm" and p["edges"][0] == 1 and p["edges"][-1] == 300 and p["alpha"][0] == 0.0
