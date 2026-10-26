// Builds ../coastlines.geojson: Natural Earth 10m country outlines (coasts + borders)
// cropped to the map region. Public-domain data via the `world-atlas` npm package.
//   cd tools && npm install world-atlas@2 topojson-client@3 && node make_coastlines.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { feature } from "topojson-client";

const [W, S, E, N] = [98, -6, 120, 8], PAD = 2;
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
