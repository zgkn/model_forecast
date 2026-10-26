// Builds ../coastlines.geojson (Natural Earth 10m country outlines: coasts + borders) and
// ../land.geojson (10m land polygons, used for the land mask), both cropped to the map region. Public-domain data via the `world-atlas` npm package.
//   cd tools && npm install world-atlas@2 topojson-client@3 && node make_coastlines.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { feature } from "topojson-client";

const [W, S, E, N] = [88, -12, 150, 30], PAD = 2;
const topo = JSON.parse(readFileSync("node_modules/world-atlas/countries-10m.json"));
const r = (v) => Math.round(v * 100) / 100;
const touches = (ring) => ring.some(([x, y]) => x > W - PAD && x < E + PAD && y > S - PAD && y < N + PAD);

const lines = [];
for (const f of feature(topo, topo.objects.countries).features) {
  const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
  for (const poly of polys) {
    for (const ring of poly) {                      // outer ring and holes (lakes) alike
      if (!touches(ring)) continue;
      const out = [];
      for (const [x, y] of ring.map(([x, y]) => [r(x), r(y)])) {
        const p = out[out.length - 1];
        if (!p || p[0] !== x || p[1] !== y) out.push([x, y]);
      }
      if (out.length > 3) lines.push(out);
    }
  }
}
writeFileSync("../coastlines.geojson", JSON.stringify({ type: "MultiLineString", coordinates: lines }));
console.log(lines.length, "rings");

// Sutherland-Hodgman clip of a ring to the (padded) region rectangle; the mask only needs the interior
const [cx0, cy0, cx1, cy1] = [W - 1, S - 1, E + 1, N + 1];
const edges = [
  [([x]) => x >= cx0, (a, b) => [cx0, a[1] + (b[1] - a[1]) * (cx0 - a[0]) / (b[0] - a[0])]],
  [([x]) => x <= cx1, (a, b) => [cx1, a[1] + (b[1] - a[1]) * (cx1 - a[0]) / (b[0] - a[0])]],
  [([, y]) => y >= cy0, (a, b) => [a[0] + (b[0] - a[0]) * (cy0 - a[1]) / (b[1] - a[1]), cy0]],
  [([, y]) => y <= cy1, (a, b) => [a[0] + (b[0] - a[0]) * (cy1 - a[1]) / (b[1] - a[1]), cy1]],
];
function clip(ring) {
  let pts = ring.slice(0, -1);
  for (const [inside, cut] of edges) {
    const out = [];
    pts.forEach((p, i) => {
      const q = pts[(i + pts.length - 1) % pts.length];
      if (inside(p)) { if (!inside(q)) out.push(cut(q, p)); out.push(p); }
      else if (inside(q)) out.push(cut(q, p));
    });
    pts = out;
    if (!pts.length) return null;
  }
  const rr = pts.map(([x, y]) => [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]);
  return rr.length > 2 ? [...rr, rr[0]] : null;
}

const land = JSON.parse(readFileSync("node_modules/world-atlas/land-10m.json"));
const polys = [];
for (const f of feature(land, land.objects.land).features) {
  const parts = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
  for (const poly of parts) {
    if (!touches(poly[0])) continue;
    const rings = poly.map(clip).filter(Boolean);    // outer ring first, holes after
    if (rings.length && poly.length && clip(poly[0])) polys.push(rings);
  }
}
writeFileSync("../land.geojson", JSON.stringify({ type: "MultiPolygon", coordinates: polys }));
console.log(polys.length, "land polygons");
