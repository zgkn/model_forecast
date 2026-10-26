"""Latest ECMWF ensemble meteogram (EPSgram) image for a point, from ECMWF Open Charts.

The Open Charts API (https://charts.ecmwf.int/opencharts-api/v1/products/<product>/?<params>) answers
with JSON that points to the generated image, so: ask for a base time, read the link, download the
image. Ensemble runs start at 00 and 12 UTC and the meteogram appears some hours later, so the newest
base times are tried first and older ones are used until one works. Images are cached per base time
(eps-<name>_<YYYYMMDDHH>.<ext>); only the newest is kept, and it is reused if ECMWF is unreachable.

No network is needed to test it: pass a fake `session`.
"""
import datetime as dt
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

API = "https://charts.ecmwf.int/opencharts-api/v1/products/opencharts_meteogram/"
PAGE = "https://charts.ecmwf.int/products/opencharts_meteogram"
EPSGRAM = "classical_10d"
SINGAPORE = {"name": "Singapore", "lat": 1.28967, "lon": 103.85}
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg", "image/webp": "webp"}
HEADERS = {"User-Agent": "model_forecast (https://github.com/zgkn/model_forecast)"}
TIMEOUT = (10, 90)   # the chart is generated on request, which can take a while


def base_time_str(t):
    return f"{t:%Y%m%d%H%M}"


def candidate_base_times(now=None, n=4):
    """Newest 00/12 UTC base times first."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).replace(
        minute=0, second=0, microsecond=0, tzinfo=None)
    latest = now - dt.timedelta(hours=now.hour % 12)
    return [latest - dt.timedelta(hours=12 * i) for i in range(n)]


def page_url(base_time, place=SINGAPORE):
    """The Open Charts web page of the same chart (for a link)."""
    return (f"{PAGE}?base_time={base_time_str(base_time)}&epsgram={EPSGRAM}"
            f"&lat={place['lat']}&lon={place['lon']}&station_name={place['name']}")


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def find_image_url(doc):
    """The image link in an Open Charts JSON answer (data.link.href, else any image-looking URL)."""
    data = doc.get("data", doc) if isinstance(doc, dict) else doc
    link = data.get("link") if isinstance(data, dict) else None
    if isinstance(link, dict) and isinstance(link.get("href"), str):
        return urljoin(API, link["href"])
    for s in _strings(doc):
        if re.match(r"(https?:)?//|/", s) and re.search(r"\.(png|jpe?g|svg|webp)(\?|$)", s, re.I):
            return urljoin(API, s)
    return None


def _image(resp):
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    return (EXT[ctype], resp.content) if resp.status_code == 200 and ctype in EXT else None


def fetch_image(session, base_time, place=SINGAPORE):
    """(extension, bytes) of the meteogram for a base time; raises RuntimeError with what came back."""
    params = {"epsgram": EPSGRAM, "base_time": base_time_str(base_time),
              "lat": place["lat"], "lon": place["lon"], "station_name": place["name"]}
    r = session.get(API, params=params, headers=HEADERS, timeout=TIMEOUT)
    direct = _image(r)
    if direct:
        return direct
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]!r}")
    try:
        url = find_image_url(r.json())
    except ValueError:
        raise RuntimeError(f"not JSON and not an image ({r.headers.get('content-type')}): {r.text[:200]!r}")
    if not url:
        raise RuntimeError(f"no image link in the answer: {r.text[:300]!r}")
    img = session.get(url, headers=HEADERS, timeout=TIMEOUT)
    got = _image(img)
    if not got:
        raise RuntimeError(f"{url} gave HTTP {img.status_code} {img.headers.get('content-type')}")
    return got


def cache_file(cache_dir, place, base_time, ext):
    return Path(cache_dir) / f"eps-{place['name'].lower()}_{base_time:%Y%m%d%H}.{ext}"


def newest_cached(cache_dir, place=SINGAPORE):
    files = sorted(Path(cache_dir).glob(f"eps-{place['name'].lower()}_*.*"))
    return files[-1] if files else None


def base_time_of(path):
    return dt.datetime.strptime(Path(path).stem.split("_")[1], "%Y%m%d%H")


def fetch(cache_dir, place=SINGAPORE, now=None, session=None, log=print):
    """Newest available meteogram as a file in cache_dir (older ones are deleted).

    A candidate that is already cached is used without any request; if ECMWF cannot be reached the newest
    cached image is returned (its base time is shown on the page), and only without any cache is an error raised.
    """
    session = session or requests.Session()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    problems = []
    for t in candidate_base_times(now):
        cached = [p for p in cache_dir.glob(f"eps-{place['name'].lower()}_{t:%Y%m%d%H}.*")]
        if cached:
            path = cached[0]
        else:
            try:
                ext, content = fetch_image(session, t, place)
            except Exception as e:        # not published yet, or a transient error: try the previous run
                problems.append(f"{t:%Y-%m-%d %H}Z: {e}")
                log(f"EPSgram {t:%Y-%m-%d %H}Z not available: {e}")
                continue
            path = cache_file(cache_dir, place, t, ext)
            path.write_bytes(content)
            log(f"EPSgram {t:%Y-%m-%d %H}Z saved ({len(content) / 1e3:.0f} kB)")
        for old in cache_dir.glob(f"eps-{place['name'].lower()}_*.*"):
            if old != path:
                old.unlink()
        return path
    stale = newest_cached(cache_dir, place)
    if stale:
        log(f"EPSgram: ECMWF gave nothing new, keeping {stale.name}")
        return stale
    raise RuntimeError("no EPSgram available: " + "; ".join(problems))
