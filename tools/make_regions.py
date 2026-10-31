"""Builds ../regions.geojson: the province and country outlines used for the area time series (Natural Earth 10m, public domain).

    python tools/make_regions.py [--admin1 ne_10m_admin_1_states_provinces.geojson] [--admin0 ne_10m_admin_0_countries.geojson]
                                 [--mys2 geoBoundaries-MYS-ADM2_simplified.geojson]

Districts come from geoBoundaries (gbOpen, CC BY 4.0, https://www.geoboundaries.org), the rest from Natural Earth.
Files that are not given are downloaded from the natural-earth-vector / geoBoundaries repositories (about 40, 13 and 1 MB). Only the areas
listed in REGIONS are kept; their outlines are rounded to 0.01 degrees, simplified (Douglas-Peucker, 0.01 degree) and
islands smaller than about 10 km2 are dropped, which leaves a few tens of KB.
"""
import argparse
import math
import json
import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
URLS = {"admin1": BASE + "ne_10m_admin_1_states_provinces.geojson", "admin0": BASE + "ne_10m_admin_0_countries.geojson",
        "mys2": "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/MYS/ADM2/geoBoundaries-MYS-ADM2_simplified.geojson"}
OUT = Path(__file__).resolve().parent.parent / "regions.geojson"
TOL = 0.01                      # simplification tolerance, degrees
MIN_AREA = 0.001                # smallest ring kept, square degrees (about 12 km2 at the equator)

# several admin-1 areas merged into one: id: (country, ISO 3166-2 codes, display name, menu group); Kyushu is the 7 prefectures of the island
MERGED = {
    "kyushu": ("Japan", ["JP-40", "JP-41", "JP-42", "JP-43", "JP-44", "JP-45", "JP-46"], "Kyushu (Japan)", "Japan"),
}

# id: (level, Natural Earth country (admin-1) / None, Natural Earth name, display name, menu group)
REGIONS = {
    "riau": ("admin1", "Indonesia", "Riau", "Riau", "Provinces"),
    "jambi": ("admin1", "Indonesia", "Jambi", "Jambi", "Provinces"),
    "ssum": ("admin1", "Indonesia", "Sumatera Selatan", "South Sumatra", "Provinces"),
    "wkal": ("admin1", "Indonesia", "Kalimantan Barat", "West Kalimantan", "Provinces"),
    "ckal": ("admin1", "Indonesia", "Kalimantan Tengah", "Central Kalimantan", "Provinces"),
    "skal": ("admin1", "Indonesia", "Kalimantan Selatan", "South Kalimantan", "Provinces"),
    "khm": ("admin0", None, "Cambodia", "Cambodia", "Countries"),
    "ktinggi": ("mys2", None, "Kota Tinggi", "Kota Tinggi (Johor)", "Districts"),
    "mersing": ("mys2", None, "Mersing", "Mersing (Johor)", "Districts"),
}


def ring_area(ring):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:]))) / 2


def rdp(pts, tol):
    """Douglas-Peucker on an open polyline (iterative, keeps both ends)."""
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
        worst, idx = 0.0, None
        for i in range(a + 1, b):
            d = abs(dy * (pts[i][0] - ax) - dx * (pts[i][1] - ay)) / norm
            if d > worst:
                worst, idx = d, i
        if idx is not None and worst > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def simplify_ring(ring):
    ring = [(round(x, 2), round(y, 2)) for x, y in ring]
    dedup = [p for i, p in enumerate(ring) if i == 0 or p != ring[i - 1]]
    if len(dedup) < 4:
        return None
    out = rdp(dedup[:-1], TOL) + [dedup[0]]          # the ring is closed: simplify the open part
    return out if len(out) >= 4 else None


def simplify(geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    out = []
    for poly in polys:
        outer = simplify_ring(poly[0])
        if outer is None or ring_area(outer) < MIN_AREA:
            continue
        holes = [r for r in (simplify_ring(h) for h in poly[1:]) if r and ring_area(r) >= MIN_AREA]
        out.append([[list(p) for p in outer]] + [[list(p) for p in h] for h in holes])
    return {"type": "MultiPolygon", "coordinates": out}


def load(path, level):
    return json.loads(Path(path).read_text()) if path else json.load(urllib.request.urlopen(URLS[level], timeout=300))


def dissolve(geoms, min_lat=29.5, res=0.005):
    """One MultiPolygon from several touching ones, with the borders between them removed (rasterise, then vectorise).

    Rings whose mean latitude is below min_lat are left out (the far southern islands of Kagoshima).
    """
    from rasterio.features import rasterize, shapes
    from rasterio.transform import from_origin
    polys = []
    for g in geoms:
        polys += [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    polys = [p for p in polys if sum(pt[1] for pt in p[0]) / len(p[0]) >= min_lat]
    xs = [pt[0] for p in polys for pt in p[0]]
    ys = [pt[1] for p in polys for pt in p[0]]
    west, north = min(xs) - 0.02, max(ys) + 0.02
    shape = (math.ceil((north - (min(ys) - 0.02)) / res), math.ceil((max(xs) + 0.02 - west) / res))
    transform = from_origin(west, north, res, res)
    mask = rasterize([{"type": "Polygon", "coordinates": p} for p in polys], out_shape=shape, transform=transform, fill=0, dtype="uint8")
    parts = [g["coordinates"] for g, v in shapes(mask, mask=mask == 1, transform=transform, connectivity=8)]
    return {"type": "MultiPolygon", "coordinates": parts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin1")
    ap.add_argument("--admin0")
    ap.add_argument("--mys2")
    args = ap.parse_args()
    paths = {"admin1": args.admin1, "admin0": args.admin0, "mys2": args.mys2}
    index = {}
    for level in {r[0] for r in REGIONS.values()}:
        data = load(paths[level], level)
        for f in data["features"]:
            p = f["properties"]
            if level == "admin1":
                index[(level, p.get("admin"), p["name"])] = f
                index[("iso", p.get("admin"), p.get("iso_3166_2"))] = f
            elif level == "admin0":
                index[(level, None, p["NAME"])] = f
            else:
                index[(level, None, p["shapeName"])] = f
    feats = []
    for id_, (level, country, name, label, group) in REGIONS.items():
        f = index.get((level, country, name))
        if f is None:
            sys.exit(f"{level} {country or ''} / {name} not found in the Natural Earth file")
        g = simplify(f["geometry"])
        feats.append({"type": "Feature", "properties": {"id": id_, "name": label, "group": group, "ne_name": name}, "geometry": g})
        print(id_, label, len(g["coordinates"]), "polygons,", sum(len(r) for p in g["coordinates"] for r in p), "points")
    for id_, (country, isos, label, group) in MERGED.items():
        parts = []
        for iso in isos:
            f = index.get(("iso", country, iso))
            if f is None:
                sys.exit(f"{country} {iso} not found in the Natural Earth admin-1 file")
            parts.append(f["geometry"])
        g = simplify(dissolve(parts))
        feats.append({"type": "Feature", "properties": {"id": id_, "name": label, "group": group, "ne_name": "+".join(isos)}, "geometry": g})
        print(id_, label, len(g["coordinates"]), "polygons,", sum(len(r) for p in g["coordinates"] for r in p), "points")
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    print("wrote", OUT, OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
