"""Builds ../regions.geojson: the province outlines used for the area time series (Natural Earth 10m admin-1, public domain).

    python tools/make_regions.py [path/to/ne_10m_admin_1_states_provinces.geojson]

Without an argument the file is downloaded from the natural-earth-vector repository (about 40 MB). Only the provinces
listed in REGIONS are kept; their outlines are rounded to 0.01 degrees, simplified (Douglas-Peucker, 0.01 degree) and
islands smaller than about 10 km2 are dropped, which leaves a few hundred KB.
"""
import json
import sys
import urllib.request
from pathlib import Path

URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_1_states_provinces.geojson"
OUT = Path(__file__).resolve().parent.parent / "regions.geojson"
TOL = 0.01                      # simplification tolerance, degrees
MIN_AREA = 0.001                # smallest ring kept, square degrees (about 12 km2 at the equator)

# id: (Natural Earth country, Natural Earth name, display name)
REGIONS = {
    "riau": ("Indonesia", "Riau", "Riau"),
    "jambi": ("Indonesia", "Jambi", "Jambi"),
    "ssum": ("Indonesia", "Sumatera Selatan", "South Sumatra"),
    "wkal": ("Indonesia", "Kalimantan Barat", "West Kalimantan"),
    "ckal": ("Indonesia", "Kalimantan Tengah", "Central Kalimantan"),
    "skal": ("Indonesia", "Kalimantan Selatan", "South Kalimantan"),
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


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    data = json.loads(src.read_text()) if src else json.load(urllib.request.urlopen(URL, timeout=300))
    by_name = {(f["properties"]["admin"], f["properties"]["name"]): f for f in data["features"]}
    feats = []
    for id_, (country, name, label) in REGIONS.items():
        f = by_name.get((country, name))
        if f is None:
            sys.exit(f"{country} / {name} not found in the admin-1 file")
        g = simplify(f["geometry"])
        feats.append({"type": "Feature", "properties": {"id": id_, "name": label, "ne_name": name, "iso": f["properties"].get("iso_3166_2")},
                      "geometry": g})
        print(id_, label, len(g["coordinates"]), "polygons,", sum(len(r) for p in g["coordinates"] for r in p), "points")
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    print("wrote", OUT, OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
