"""Tests for epsgram.py with a fake HTTP session (no network)."""
import datetime as dt

import pytest

import epsgram

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40


class Resp:
    def __init__(self, status=200, json=None, content=b"", ctype="application/json", text=None):
        self.status_code, self._json, self.content = status, json, content
        self.headers = {"content-type": ctype}
        self.text = text if text is not None else (str(json) if json is not None else "")

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class Session:
    """Answers API calls from {base_time: Resp}; image URLs from `images`."""

    def __init__(self, api, images=None):
        self.api, self.images, self.calls = api, images or {}, []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        if params is not None:
            return self.api.get(params["base_time"], Resp(404, text="no such chart"))
        return self.images.get(url, Resp(404))


def ok_json(href="https://charts.ecmwf.int/img/abc.png"):
    return Resp(json={"data": {"link": {"href": href}}})


NOW = dt.datetime(2026, 10, 26, 7, 30, tzinfo=dt.timezone.utc)


def test_candidates_are_the_latest_00_and_12z_runs():
    c = epsgram.candidate_base_times(NOW)
    assert c[0] == dt.datetime(2026, 10, 26, 0) and c[1] == dt.datetime(2026, 10, 25, 12)
    assert epsgram.candidate_base_times(dt.datetime(2026, 10, 26, 13, 5, tzinfo=dt.timezone.utc))[0].hour == 12


def test_find_image_url_variants():
    assert epsgram.find_image_url({"data": {"link": {"href": "https://x/y.png"}}}) == "https://x/y.png"
    assert epsgram.find_image_url({"data": {"link": {"href": "/media/y"}}}).endswith("/media/y")
    assert epsgram.find_image_url({"a": [{"b": "https://x/chart.svg?v=2"}]}) == "https://x/chart.svg?v=2"
    assert epsgram.find_image_url({"data": {"attributes": {"title": "no link"}}}) is None


def test_walks_back_to_the_newest_available_run_and_keeps_only_it(tmp_path):
    (tmp_path / "eps-singapore_2026102412.png").write_bytes(b"old")
    s = Session({"202610251200": ok_json()}, {"https://charts.ecmwf.int/img/abc.png": Resp(content=PNG, ctype="image/png")})
    path = epsgram.fetch(tmp_path, now=NOW, session=s, log=lambda m: None)
    assert path.name == "eps-singapore_2026102512.png" and path.read_bytes() == PNG
    assert [p.name for p in tmp_path.iterdir()] == [path.name]                  # older run deleted
    assert [c[1]["base_time"] for c in s.calls if c[1]] == ["202610260000", "202610251200"]
    assert s.calls[0][1]["epsgram"] == "classical_10d" and s.calls[0][1]["station_name"] == "Singapore"


def test_cached_candidate_needs_no_download(tmp_path):
    (tmp_path / "eps-singapore_2026102512.png").write_bytes(PNG)
    s = Session({})                                                             # 26 Oct 00Z: not published yet
    path = epsgram.fetch(tmp_path, now=NOW, session=s, log=lambda m: None)
    assert path.name == "eps-singapore_2026102512.png"
    assert len(s.calls) == 1                                                    # only the 00Z attempt


def test_answer_that_is_the_image_itself_is_accepted(tmp_path):
    s = Session({"202610260000": Resp(content=PNG, ctype="image/png")})
    assert epsgram.fetch(tmp_path, now=NOW, session=s, log=lambda m: None).suffix == ".png"


def test_a_link_that_is_not_an_image_is_skipped(tmp_path):
    s = Session({"202610260000": ok_json("https://x/a.png"), "202610251200": ok_json("https://x/b.png")},
                {"https://x/a.png": Resp(content=b"<html>", ctype="text/html"),
                 "https://x/b.png": Resp(content=PNG, ctype="image/png")})
    assert epsgram.fetch(tmp_path, now=NOW, session=s, log=lambda m: None).name.endswith("2026102512.png")


def test_unreachable_ecmwf_keeps_the_stale_image_or_raises(tmp_path):
    with pytest.raises(RuntimeError, match="no EPSgram available"):
        epsgram.fetch(tmp_path, now=NOW, session=Session({}), log=lambda m: None)
    (tmp_path / "eps-singapore_2026102212.png").write_bytes(PNG)
    msgs = []
    path = epsgram.fetch(tmp_path, now=NOW, session=Session({}), log=msgs.append)
    assert path.name == "eps-singapore_2026102212.png" and any("keeping" in m for m in msgs)


def test_page_url_matches_the_open_charts_link():
    u = epsgram.page_url(dt.datetime(2026, 10, 22, 0))
    assert u == ("https://charts.ecmwf.int/products/opencharts_meteogram?base_time=202610220000&epsgram=classical_10d"
                 "&lat=1.28967&lon=103.85&station_name=Singapore")
