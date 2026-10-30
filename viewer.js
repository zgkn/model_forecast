// folium emits this block before it creates the map, so wait for page load
window.addEventListener("load", function () {
  var map = __MAP__;
  var M = __MANIFEST__;
  var G = M.grid, NX = G.nlon, NY = G.nlat, N = NX * NY;
  var LAT_N = G.lat0, LAT_S = G.lat0 - (NY - 1) * G.d;
  var LON_W = G.lon0, LON_E = G.lon0 + (NX - 1) * G.d;
  var KT = 1.943844;                    // m/s -> knots
  var MIN_ARROW_PX = 22;                // thin the arrows out below this spacing
  var PLAY_MS = 450;
  var RAD = Math.PI / 180;
  var CDD_LEVELS = [5, 10, 15];

  var el = function (id) { return document.getElementById(id); };
  var models = Object.keys(M.models);
  var state = { view: "map", lastShade: {}, model: models[0], t: 0, day: null, shade: "speed" + M.levels[0], prevShade: null,
                level: M.levels[0], style: "arrows" };
  var timer = null;

  ["wv-top", "wv-bottom", "wv-legend"].forEach(function (id) {
    L.DomEvent.disableClickPropagation(el(id));
    L.DomEvent.disableScrollPropagation(el(id));
  });

  function cddOf(m) { return M.models[m].cdd; }
  function inCdd() { return state.shade === "cdd"; }
  function noFlow() { return inCdd() || (!!M.models[state.model].shading && !M.models[state.model].flowFrom); }   // no wind with these layers
  models.forEach(function (m) { if (cddOf(m)) M.models[m].files.cdd = cddOf(m).files.cdd; });

  // ---- data loading: one binary file per model and variable, fetched on demand -----------------
  var pending = {};   // "model/name" -> Promise
  var arrays = {};    // "model/name" -> typed array once loaded

  function gunzip(buf) {
    var b = new Uint8Array(buf);
    if (b[0] !== 0x1f || b[1] !== 0x8b) return Promise.resolve(buf);   // server already decoded it
    return new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();
  }

  function load(model, name) {
    var key = model + "/" + name;
    if (!pending[key]) {
      var url = M.models[model].files[name];
      pending[key] = fetch(url)
        .then(function (r) { if (!r.ok) throw new Error(r.status + " " + url); return r.arrayBuffer(); })
        .then(gunzip)
        .then(function (buf) {
          arrays[key] = name.indexOf("uv") === 0 ? new Int16Array(buf) : name === "rain" ? new Uint16Array(buf) : new Uint8Array(buf);
          return arrays[key];
        });
      pending[key].catch(function () { delete pending[key]; });
    }
    return pending[key];
  }

  function shadeKind() { return state.shade.indexOf("speed") === 0 ? "speed" : state.shade === "rain" ? "rain" : state.shade === "cdd" ? "cdd" : state.shade === "aod" ? "aod" : "rh"; }
  function shadeFile() { return state.shade === "rh" ? "rh" + M.rhLevel : state.shade; }
  function neededFiles() {                  // [model, file] pairs for the current frame
    var m = state.model, f;
    if (inCdd()) return [[m, "cdd"]];
    f = state.shade === "aod" ? [[m, "aod"]] : [[m, shadeFile().replace(/^speed/, "uv")]];
    if (state.style !== "off" && !noFlow()) f.push([flowModel(), "uv" + state.level]);
    return f.filter(function (a, i) { return f.findIndex(function (b) { return b[0] === a[0] && b[1] === a[1]; }) === i; });
  }
  function get(name) { return arrays[state.model + "/" + name]; }

  // CAMS has no wind of its own: it borrows the flow of another model at the same valid time
  function flowModel() { return M.models[state.model].flowFrom || state.model; }
  function flowIdx() {                      // frame of the flow model valid at the current time, or -1
    var fm = flowModel();
    if (fm === state.model) return state.t;
    return M.models[fm].valid.indexOf(M.models[state.model].valid[state.t]);
  }
  function flowArr() { return arrays[flowModel() + "/uv" + state.level]; }
  function flowBase() { var i = flowIdx(); return i < 0 ? -1 : i * 2 * N; }

  // ---- colour lookup tables ---------------------------------------------------------------------
  var LUTS = {};
  function lut(kind) {
    if (LUTS[kind]) return LUTS[kind];
    var P = M.palettes[kind], vmax = { speed: 150, rain: 300, rh: 100, cdd: 40, aod: 6 }[kind], scale = { speed: 4, rain: 10, rh: 4, cdd: 1, aod: 100 }[kind];
    var n = Math.ceil(vmax * scale) + 1, t = new Uint32Array(n);
    var rgba = P.colors.map(function (c, i) {
      var r = parseInt(c.substr(1, 2), 16), g = parseInt(c.substr(3, 2), 16), b = parseInt(c.substr(5, 2), 16);
      return ((Math.round(P.alpha[i] * 255) << 24) | (b << 16) | (g << 8) | r) >>> 0;
    });
    for (var i = 0; i < n; i++) {
      var v = i / scale, k = 0;
      while (k < P.edges.length && v >= P.edges[k]) k++;
      t[i] = rgba[k];
    }
    return (LUTS[kind] = { t: t, scale: scale, n: n });
  }

  // ---- canvas layers inside leaflet panes ----------------------------------------------------------
  function canvasLayer(pane, z) {
    map.createPane(pane);
    var p = map.getPane(pane);
    p.style.zIndex = z; p.style.pointerEvents = "none";
    var c = document.createElement("canvas");
    c.style.position = "absolute";
    p.appendChild(c);
    return {
      canvas: c,
      begin: function () {                  // canvas stays aligned with the map container; draw in container px
        var s = map.getSize(), d = window.devicePixelRatio || 1;
        if (c.width !== Math.round(s.x * d) || c.height !== Math.round(s.y * d)) {
          c.width = Math.round(s.x * d); c.height = Math.round(s.y * d);
          c.style.width = s.x + "px"; c.style.height = s.y + "px";
        }
        L.DomUtil.setPosition(c, map.containerPointToLayerPoint([0, 0]));
        var ctx = c.getContext("2d");
        ctx.setTransform(d, 0, 0, d, 0, 0);
        ctx.clearRect(0, 0, s.x, s.y);
        return ctx;
      }
    };
  }
  var shadeLayer = canvasLayer("wv-shade", 410);
  map.createPane("wv-coast"); map.getPane("wv-coast").style.zIndex = 420;
  map.createPane("wv-cdd"); map.getPane("wv-cdd").style.zIndex = 430;     // dry-spell outlines (clickable)
  var flowLayer = canvasLayer("wv-flow", 450);

  fetch("data/coastlines.json").then(function (r) { return r.json(); }).then(function (gj) {
    L.geoJSON(gj, { pane: "wv-coast", interactive: false,
      style: { color: "#1a1a19", weight: 1, opacity: 0.7, fill: false } }).addTo(map);
  });

  // ---- shading: raster rendered from the grid (bilinear, 4x oversampled, mercator rows) --------------
  var S = 4, OW = (NX - 1) * S;
  function merc(lat) { return Math.log(Math.tan(Math.PI / 4 + lat * RAD / 2)); }
  var yN = merc(LAT_N), yS = merc(LAT_S);
  var OH = Math.round(OW * (yN - yS) / ((LON_E - LON_W) * RAD));
  var colI = new Int32Array(OW), colF = new Float32Array(OW), rowI = new Int32Array(OH), rowF = new Float32Array(OH);
  (function () {
    for (var x = 0; x < OW; x++) {
      var gx = (x + 0.5) / OW * (NX - 1), c0 = Math.min(Math.floor(gx), NX - 2);
      colI[x] = c0; colF[x] = gx - c0;
    }
    for (var y = 0; y < OH; y++) {
      var lat = (2 * Math.atan(Math.exp(yN - (y + 0.5) / OH * (yN - yS))) - Math.PI / 2) / RAD;
      var gy = Math.min(Math.max((LAT_N - lat) / G.d, 0), NY - 1.000001), r0 = Math.floor(gy);
      rowI[y] = r0; rowF[y] = gy - r0;
    }
  })();
  var off = document.createElement("canvas");
  off.width = OW; off.height = OH;
  var offCtx = off.getContext("2d"), offImg = offCtx.createImageData(OW, OH), offPx = new Uint32Array(offImg.data.buffer);

  // consecutive dry days: one flat-coloured block per grid cell, bounds on the cell EDGES, so the
  // raster matches the outlines (and no NaN ocean cell bleeds into the coast through interpolation)
  var CS = 4, cOW = NX * CS, cLatN = LAT_N + G.d / 2, cLatS = LAT_S - G.d / 2;
  var cYN = merc(cLatN), cYS = merc(cLatS);
  var cOH = Math.round(cOW * (cYN - cYS) / (NX * G.d * RAD));
  var cRow = new Int32Array(cOH);
  for (var cy = 0; cy < cOH; cy++) {
    var clat = (2 * Math.atan(Math.exp(cYN - (cy + 0.5) / cOH * (cYN - cYS))) - Math.PI / 2) / RAD;
    cRow[cy] = Math.min(Math.max(Math.floor((cLatN - clat) / G.d), 0), NY - 1);
  }
  var cOff = document.createElement("canvas");
  cOff.width = cOW; cOff.height = cOH;
  var cCtx = cOff.getContext("2d"), cImg = cCtx.createImageData(cOW, cOH), cPx = new Uint32Array(cImg.data.buffer);

  var field = new Float32Array(N);
  var shadeSrc = null;                       // {canvas, bounds} of the last rendered raster

  function fillField() {                    // shading variable in display units for the current frame
    var kind = shadeKind(), i;
    if (kind === "speed") {
      var uv = get(shadeFile().replace(/^speed/, "uv")), b = state.t * 2 * N, k = 0.1 * KT;
      for (i = 0; i < N; i++) field[i] = Math.hypot(uv[b + i], uv[b + N + i]) * k;
    } else if (kind === "cdd") {
      var c = get("cdd"), o = state.day * N;
      for (i = 0; i < N; i++) field[i] = c[o + i] === 255 ? NaN : c[o + i];
    } else {
      var a = get(shadeFile()), s = kind === "rain" ? 0.1 : kind === "aod" ? 0.02 : 1, o2 = state.t * N;
      for (i = 0; i < N; i++) field[i] = a[o2 + i] * s;
    }
  }

  function renderShade() {
    var kind = shadeKind(), L_ = lut(kind), T = L_.t, sc = L_.scale, nmax = L_.n - 1;
    fillField();
    if (kind === "cdd") {
      for (var y2 = 0; y2 < cOH; y2++) {
        var ro = cRow[y2] * NX, o2 = y2 * cOW;
        for (var x2 = 0; x2 < cOW; x2++) {
          var v2 = field[ro + ((x2 / CS) | 0)] * sc;           // NaN (not land) -> class 0, transparent
          cPx[o2 + x2] = T[(v2 > nmax ? nmax : v2) | 0];
        }
      }
      cCtx.putImageData(cImg, 0, 0);
      shadeSrc = { canvas: cOff, bounds: [[cLatS, LON_W - G.d / 2], [cLatN, LON_E + G.d / 2]] };
      return;
    }
    for (var y = 0; y < OH; y++) {
      var r0 = rowI[y] * NX, r1 = r0 + NX, fy = rowF[y], gy = 1 - fy, o = y * OW;
      for (var x = 0; x < OW; x++) {
        var c = colI[x], fx = colF[x];
        var top = field[r0 + c] * (1 - fx) + field[r0 + c + 1] * fx;
        var bot = field[r1 + c] * (1 - fx) + field[r1 + c + 1] * fx;
        var v = (top * gy + bot * fy) * sc;
        offPx[o + x] = T[v < 0 ? 0 : v > nmax ? nmax : v | 0];
      }
    }
    offCtx.putImageData(offImg, 0, 0);
    shadeSrc = { canvas: off, bounds: [[LAT_S, LON_W], [LAT_N, LON_E]] };
  }

  function drawShade() {
    var ctx = shadeLayer.begin();
    if (!shadeSrc) return;
    var nw = map.latLngToContainerPoint([shadeSrc.bounds[1][0], shadeSrc.bounds[0][1]]);
    var se = map.latLngToContainerPoint([shadeSrc.bounds[0][0], shadeSrc.bounds[1][1]]);
    ctx.imageSmoothingEnabled = shadeKind() !== "cdd";
    ctx.drawImage(shadeSrc.canvas, nw.x, nw.y, se.x - nw.x, se.y - nw.y);
  }

  // ---- flow: bilinear sampling of u/v (units of 0.1 m/s) ------------------------------------------------
  var su = 0, sv = 0;
  function sampleUV(arr, base, lon, lat) {
    var gx = (lon - LON_W) / G.d, gy = (LAT_N - lat) / G.d;
    if (gx < 0 || gy < 0 || gx > NX - 1 || gy > NY - 1) return false;
    var x0 = gx | 0, y0 = gy | 0;
    if (x0 >= NX - 1) x0 = NX - 2;
    if (y0 >= NY - 1) y0 = NY - 2;
    var fx = gx - x0, fy = gy - y0, i = base + y0 * NX + x0;
    var a = (1 - fx) * (1 - fy), b = fx * (1 - fy), c = (1 - fx) * fy, d = fx * fy;
    su = arr[i] * a + arr[i + 1] * b + arr[i + NX] * c + arr[i + NX + 1] * d;
    i += N;
    sv = arr[i] * a + arr[i + 1] * b + arr[i + NX] * c + arr[i + NX + 1] * d;
    return true;
  }

  function arrowPath(x, y, u, v) {          // u, v in m/s
    var sp = Math.hypot(u, v);
    if (sp < 0.5) return null;
    var len = 7 + Math.min(sp, 30) * 0.7;
    var a = Math.atan2(-v, u);               // mercator is conformal: screen angle = wind angle
    var dx = Math.cos(a) * len / 2, dy = Math.sin(a) * len / 2;
    var hx = x + dx, hy = y + dy, h = Math.min(5, len * 0.4), w = 2.6;
    var p = new Path2D();
    p.moveTo(x - dx, y - dy);
    p.lineTo(hx, hy);
    p.moveTo(hx - Math.cos(a) * h - Math.sin(a) * w, hy - Math.sin(a) * h + Math.cos(a) * w);
    p.lineTo(hx, hy);
    p.lineTo(hx - Math.cos(a) * h + Math.sin(a) * w, hy - Math.sin(a) * h - Math.cos(a) * w);
    return p;
  }

  function strokeHalo(ctx, paths, dark) {
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.strokeStyle = "rgba(255,255,255,0.8)"; ctx.lineWidth = 3.4;
    paths.forEach(function (p) { ctx.stroke(p); });
    ctx.strokeStyle = dark; ctx.lineWidth = 1.3;
    paths.forEach(function (p) { ctx.stroke(p); });
  }

  function drawArrows(ctx, uv) {
    var size = map.getSize(), b = flowBase();
    var pxPerStep = G.d * 256 * Math.pow(2, map.getZoom()) / 360;
    var k = Math.max(1, Math.ceil(MIN_ARROW_PX / pxPerStep)), paths = [];
    for (var i = 0; i < NY; i += k) {
      for (var j = 0; j < NX; j += k) {
        var pt = map.latLngToContainerPoint([LAT_N - i * G.d, LON_W + j * G.d]);
        if (pt.x < -20 || pt.y < -20 || pt.x > size.x + 20 || pt.y > size.y + 20) continue;
        var p = arrowPath(pt.x, pt.y, uv[b + i * NX + j] / 10, uv[b + N + i * NX + j] / 10);
        if (p) paths.push(p);
      }
    }
    strokeHalo(ctx, paths, "#1a1a19");
  }

  // streamlines: integrated in screen space (so the density is even at every zoom) in leaflet layer
  // coordinates, so the cached lines move with the map while panning; recomputed when the view settles.
  var streams = null;
  var CELL = 11, STEP_PX = 2.5, MAX_STEPS = 260, MARGIN = 120, MIN_SPEED = 3;   // speeds in 0.1 m/s

  function computeStreams() {
    var uv = flowArr(), base = flowBase();
    if (!uv || base < 0) { streams = null; return; }
    var size = map.getSize(), z = map.getZoom(), world = 256 * Math.pow(2, z), org = map.getPixelOrigin();
    var tl = map.containerPointToLayerPoint([0, 0]);
    var x0 = tl.x - MARGIN, y0 = tl.y - MARGIN, W = size.x + 2 * MARGIN, H = size.y + 2 * MARGIN;
    var cols = Math.ceil(W / CELL), rows = Math.ceil(H / CELL), occ = new Uint8Array(cols * rows);

    function cellOf(x, y) {
      var cx = Math.floor((x - x0) / CELL), cy = Math.floor((y - y0) / CELL);
      return (cx < 0 || cy < 0 || cx >= cols || cy >= rows) ? -1 : cy * cols + cx;
    }
    var dx = 0, dy = 0;
    function dir(x, y) {                      // unit flow direction in screen space at layer point (x, y)
      var lon = (x + org.x) / world * 360 - 180;
      var lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * (y + org.y) / world))) / RAD;
      if (!sampleUV(uv, base, lon, lat)) return false;
      var sp = Math.hypot(su, sv);
      if (sp < MIN_SPEED) return false;
      dx = su / sp; dy = -sv / sp;
      return true;
    }
    function integrate(x, y, sign, marked) {
      var out = [], last = cellOf(x, y);
      for (var n = 0; n < MAX_STEPS; n++) {
        if (!dir(x, y)) break;
        var h = sign * STEP_PX, mx = x + 0.5 * h * dx, my = y + 0.5 * h * dy;
        if (!dir(mx, my)) break;
        x += h * dx; y += h * dy;
        var c = cellOf(x, y);
        if (c < 0) break;
        if (c !== last) {
          if (occ[c]) break;
          occ[c] = 1; marked.push(c); last = c;
        }
        out.push(x, y);
      }
      return out;
    }

    var order = [];                           // seed cells, outermost ring first
    for (var i = 0; i < cols * rows; i++) order.push(i);
    var ring = function (c) { var cx = c % cols, cy = (c / cols) | 0; return Math.min(cx, cy, cols - 1 - cx, rows - 1 - cy); };
    order.sort(function (a, b) { return ring(a) - ring(b); });

    var lines = [], heads = [];
    for (var s = 0; s < order.length; s++) {
      var c0 = order[s];
      if (occ[c0]) continue;
      var sx = x0 + ((c0 % cols) + 0.5) * CELL, sy = y0 + (((c0 / cols) | 0) + 0.5) * CELL;
      if (!dir(sx, sy)) continue;
      var marked = [c0]; occ[c0] = 1;
      var fwd = integrate(sx, sy, 1, marked), bwd = integrate(sx, sy, -1, marked);
      if (fwd.length + bwd.length < 14) { marked.forEach(function (m) { occ[m] = 0; }); continue; }
      var pts = [];
      for (var k = bwd.length - 2; k >= 0; k -= 2) pts.push(bwd[k], bwd[k + 1]);
      pts.push(sx, sy);
      for (k = 0; k < fwd.length; k++) pts.push(fwd[k]);
      lines.push(pts);
      var m = (pts.length / 4 | 0) * 2;       // arrow head at the middle, pointing downstream
      heads.push(pts[m], pts[m + 1], Math.atan2(pts[m + 3] - pts[m + 1], pts[m + 2] - pts[m]));
    }
    streams = { lines: lines, heads: heads };
  }

  function drawStreams(ctx) {
    if (!streams) return;
    var tl = map.containerPointToLayerPoint([0, 0]);
    ctx.save();
    ctx.translate(-tl.x, -tl.y);
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    var path = new Path2D(), tri = new Path2D(), i;
    streams.lines.forEach(function (p) {
      path.moveTo(p[0], p[1]);
      for (var k = 2; k < p.length; k += 2) path.lineTo(p[k], p[k + 1]);
    });
    for (i = 0; i < streams.heads.length; i += 3) {
      var x = streams.heads[i], y = streams.heads[i + 1], a = streams.heads[i + 2], c = Math.cos(a), s = Math.sin(a);
      tri.moveTo(x + 4.5 * c, y + 4.5 * s);
      tri.lineTo(x - 3 * c - 2.6 * s, y - 3 * s + 2.6 * c);
      tri.lineTo(x - 3 * c + 2.6 * s, y - 3 * s - 2.6 * c);
      tri.closePath();
    }
    ctx.strokeStyle = "rgba(255,255,255,0.7)"; ctx.lineWidth = 2.8; ctx.stroke(path);
    ctx.strokeStyle = "#1a1a19"; ctx.lineWidth = 0.9; ctx.stroke(path);
    ctx.fillStyle = "#1a1a19"; ctx.fill(tri);
    ctx.restore();
  }

  function drawFlow() {
    var ctx = flowLayer.begin();
    if (state.style === "off" || noFlow()) return;     // wind has no meaning on the dry-day axis
    var uv = flowArr();
    if (!uv || flowBase() < 0) return;
    if (state.style === "arrows") drawArrows(ctx, uv); else drawStreams(ctx);
  }

  // ---- scheduling -----------------------------------------------------------------------------------------
  var q = { draw: false, streams: false };
  function schedule(recomputeStreams) {
    if (recomputeStreams) q.streams = true;
    if (q.draw) return;
    q.draw = true;
    requestAnimationFrame(function () {
      q.draw = false;
      if (q.streams) { q.streams = false; if (state.style === "streams" && !noFlow()) computeStreams(); }
      drawShade(); drawFlow();
    });
  }
  map.on("move resize", function () { schedule(false); });
  map.on("moveend zoomend", function () { schedule(true); });
  map.on("zoomstart", function () { shadeLayer.canvas.style.visibility = flowLayer.canvas.style.visibility = "hidden"; });
  map.on("zoomend", function () { shadeLayer.canvas.style.visibility = flowLayer.canvas.style.visibility = "visible"; });

  // ---- dry-spell outlines (>= 5 / 10 / 15 days), one layer group per model ---------------------------------------
  var OUTLINE = { 5: { weight: 1.3, dashArray: "5 4" }, 10: { weight: 1.7 }, 15: { weight: 3 } };
  var regionLayers = {};                    // model -> Promise<{level: L.GeoJSON}>

  function dayLabel(iso, opts) {
    return new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", Object.assign({ timeZone: "UTC" }, opts));
  }

  function regionsFor(model) {
    var c = cddOf(model);
    if (!c) return Promise.resolve(null);
    if (!regionLayers[model]) {
      regionLayers[model] = fetch(c.files.regions).then(function (r) { return r.json(); }).then(function (gj) {
        var out = {};
        CDD_LEVELS.forEach(function (lv) {
          out[lv] = L.geoJSON(gj, {
            pane: "wv-cdd",
            filter: function (f) { return f.properties.level === lv; },
            style: Object.assign({ color: "#1a1a19", opacity: 0.95, fill: true, fillOpacity: 0 }, OUTLINE[lv]),
            onEachFeature: function (f, layer) {
              var p = f.properties, km2 = Number(p.area.split(" ")[0]).toLocaleString("en-GB");
              layer.bindPopup("<b>≥ " + lv + " consecutive dry days</b><br>Earliest: " +
                dayLabel(p.earliest, { weekday: "short", day: "2-digit", month: "short" }) +
                "<br>Peak: " + p.peak + "<br>Area: " + km2 + " km²",
                { autoPanPaddingTopLeft: [20, 170], autoPanPaddingBottomRight: [20, 110] });
            }
          });
        });
        return out;
      });
      regionLayers[model].catch(function () { delete regionLayers[model]; });
    }
    return regionLayers[model];
  }

  function syncOutlines() {
    Object.keys(regionLayers).forEach(function (m) {
      regionLayers[m].then(function (layers) { CDD_LEVELS.forEach(function (lv) { map.removeLayer(layers[lv]); }); });
    });
    if (!inCdd()) return;
    var m = state.model;
    regionsFor(m).then(function (layers) {
      if (!layers || m !== state.model || !inCdd()) return;
      CDD_LEVELS.forEach(function (lv) {
        var box = document.querySelector('#wv-cdd-controls input[data-level="' + lv + '"]');
        if (box && box.checked) layers[lv].addTo(map);
      });
    });
  }

  // ---- labels, legend, readout ----------------------------------------------------------------------------------
  function fmt(sec, opts) {
    return new Date(sec * 1000).toLocaleString("en-GB", Object.assign({ timeZone: "UTC", hour12: false }, opts)).replace(",", "");
  }
  function validLabel(m, t) {
    var d = M.models[m];
    return fmt(d.valid[t], { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) +
      " UTC · +" + d.steps[t] + " h";
  }
  function shortLabel(m, t) {
    return fmt(M.models[m].valid[t], { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) + "Z";
  }

  function legendTitle() {
    var kind = shadeKind();
    if (kind === "speed") return "Wind speed at " + state.shade.replace("speed", "") + " hPa (knots)";
    if (kind === "rh") return "Relative humidity at " + M.rhLevel + " hPa (%)";
    if (kind === "cdd") return "Consecutive dry days: longest spell so far";
    if (kind === "aod") return "Organic matter aerosol optical depth at 550 nm (CAMS)";
    var iv = M.models[state.model].interval[state.t];
    return iv ? "Rain in the preceding " + iv + " h (mm)" : "Rain (none at +0 h)";
  }
  function drawLegend() {
    var kind = shadeKind(), P = M.palettes[kind], bar = el("wv-legend-bar"), ticks = el("wv-legend-ticks");
    el("wv-legend-title").textContent = legendTitle();
    var note = el("wv-legend-note");
    note.hidden = kind !== "cdd" && kind !== "aod";
    if (kind === "aod") note.textContent = "Generated using Copernicus Atmosphere Monitoring Service information.";
    if (kind === "cdd") {
      note.textContent = "Days under " + cddOf(state.model).dryMm + " mm (UTC), counted from the first forecast day. " +
        "Outlines: ≥ 5 d dashed, ≥ 10 d thin, ≥ 15 d thick.";
    }
    if (bar.dataset.kind !== kind) {
      bar.dataset.kind = kind;
      bar.innerHTML = ""; ticks.innerHTML = "";
      P.colors.forEach(function (c) {
        var d = document.createElement("span"); d.style.background = c; bar.appendChild(d);
      });
      P.edges.forEach(function (v, i) {
        if (P.ticks && P.ticks.indexOf(v) < 0) return;
        var t = document.createElement("span");
        t.textContent = P.ticks && v === P.edges[P.edges.length - 1] ? v + "+" : v;
        t.style.left = ((i + 1) / P.colors.length * 100) + "%";
        ticks.appendChild(t);
      });
    }
  }

  var ro = el("wv-readout");
  map.on("mousemove", function (e) {
    var parts = [Math.abs(e.latlng.lat).toFixed(1) + "°" + (e.latlng.lat < 0 ? "S" : "N") + " " +
      e.latlng.lng.toFixed(1) + "°E"];
    var gx = (e.latlng.lng - LON_W) / G.d, gy = (LAT_N - e.latlng.lat) / G.d;
    var inside = gx >= -0.5 && gy >= -0.5 && gx <= NX - 0.5 && gy <= NY - 0.5;
    if (inCdd()) {
      if (shadeSrc && inside) {
        var c = field[Math.min(NY - 1, Math.max(0, Math.round(gy))) * NX + Math.min(NX - 1, Math.max(0, Math.round(gx)))];
        parts.push(isNaN(c) ? "not land" : "longest dry spell so far: " + c + " days");
      }
    } else {
      var shadeArr = get(shadeFile().replace(/^speed/, "uv")) || get(shadeFile());
      var uv = noFlow() ? null : flowArr();
      if (uv && flowBase() >= 0 && state.style !== "off" && sampleUV(uv, flowBase(), e.latlng.lng, e.latlng.lat)) {
        var kt = Math.hypot(su, sv) * 0.1 * KT, from = (270 - Math.atan2(sv, su) / RAD + 360) % 360;
        parts.push(state.level + " hPa " + (flowModel() === state.model ? "" : M.models[flowModel()].label + " ") + "wind " + kt.toFixed(0) + " kt from " + String(Math.round(from) % 360).padStart(3, "0") + "°");
      }
      if (shadeArr && shadeSrc && state.shade !== "speed" + state.level && gx >= 0 && gy >= 0 && gx <= NX - 1 && gy <= NY - 1) {
        var v = field[Math.round(gy) * NX + Math.round(gx)];
        parts.push(state.shade.indexOf("speed") === 0 ? state.shade.replace("speed", "") + " hPa wind " + v.toFixed(0) + " kt" :
          state.shade === "rain" ? v.toFixed(1) + " mm" : state.shade === "aod" ? "organic matter AOD " + v.toFixed(2) : "RH " + v.toFixed(0) + "%");
      }
    }
    ro.textContent = parts.join(" · "); ro.hidden = false;
  });
  map.on("mouseout", function () { ro.hidden = true; });

  // ---- time axis: forecast steps, or UTC days when the dry-day layer is shown -------------------------------------------
  var slider = el("wv-slider"), status = el("wv-status"), token = 0;

  function axisLen() { return inCdd() ? cddOf(state.model).dates.length : M.models[state.model].steps.length; }
  function axisIdx() { return inCdd() ? state.day : state.t; }
  function nearest(values, target) {
    var best = 0;
    values.forEach(function (v, i) { if (Math.abs(v - target) < Math.abs(values[best] - target)) best = i; });
    return best;
  }
  function dayMs(iso) { return Date.parse(iso + "T00:00:00Z"); }

  function timeLabel() {
    if (inCdd()) {
      var c = cddOf(state.model), n = c.dates.length;
      return "Dry spells through " + dayLabel(c.dates[state.day], { weekday: "short", day: "2-digit", month: "short" }) +
        " (UTC day " + (state.day + 1) + " of " + n + ")";
    }
    return validLabel(state.model, state.t);
  }
  function flowNote() {                    // which model's wind is drawn on a tab that has none of its own
    if (!M.models[state.model].flowFrom || state.style === "off") return "";
    var f = M.models[flowModel()];
    return " · " + state.level + " hPa wind: " + f.label + " " + fmt(f.run, { day: "2-digit", month: "short", hour: "2-digit" }) + "Z run" +
      (flowIdx() < 0 ? " (not available at this time)" : "");
  }
  function updateAxis() {
    var d = M.models[state.model], n = axisLen();
    slider.max = n - 1; slider.value = axisIdx();
    if (inCdd()) {
      var c = cddOf(state.model);
      el("wv-first").textContent = dayLabel(c.dates[0], { day: "2-digit", month: "short" });
      el("wv-last").textContent = dayLabel(c.dates[n - 1], { day: "2-digit", month: "short" });
      el("wv-run").textContent = d.long + " · CDD from run " +
        fmt(c.run, { day: "2-digit", month: "short", hour: "2-digit" }) + " UTC";
    } else {
      el("wv-first").textContent = shortLabel(state.model, 0);
      el("wv-last").textContent = shortLabel(state.model, n - 1);
      el("wv-run").textContent = d.long + " · run " + fmt(d.run, { day: "2-digit", month: "short", hour: "2-digit" }) + " UTC" + flowNote();
    }
  }

  function setStatus(msg) { status.textContent = msg || ""; status.hidden = !msg; }

  function refresh() {                      // make sure the needed files are loaded, then render
    var names = neededFiles(), tk = ++token;
    drawLegend(); updateAxis();
    el("wv-valid").textContent = timeLabel();
    var missing = names.filter(function (a) { return !arrays[a[0] + "/" + a[1]]; });
    if (!missing.length) return render();
    setStatus("Loading…");
    Promise.all(missing.map(function (a) { return load(a[0], a[1]); })).then(function () {
      if (tk !== token) return;
      setStatus(""); render();
    }).catch(function (err) { if (tk === token) setStatus("Could not load data: " + err.message); });
  }

  function render() {
    renderShade();
    schedule(true);
  }

  function setFrame(i) {
    i = Math.max(0, Math.min(axisLen() - 1, i));
    if (inCdd()) state.day = i; else state.t = i;
    refresh();
  }

  function syncControls() {
    var cdd = inCdd(), noWind = noFlow();
    el("wv-level").disabled = noWind;
    document.querySelectorAll("#wv-style button").forEach(function (b) { b.disabled = noWind; });
    el("wv-cdd-controls").hidden = !cdd;
    if (cdd && cddOf(state.model)) el("wv-nc").href = cddOf(state.model).files.nc;
    var opt = shadeSel.querySelector('option[value="cdd"]');
    if (opt) opt.disabled = !cddOf(state.model);
    syncOutlines();
  }

  // each model offers its own shadings (CAMS only has its AOD); the choice is remembered per kind of model
  function shadeGroup(m) { return M.models[m].shading ? "own" : "nwp"; }
  function shadeOptionsFor(model) {
    var d = M.models[model];
    if (d.shading) return d.shading.map(function (o) { return [o.value, o.label]; });
    var o = M.levels.map(function (l) { return ["speed" + l, "Wind speed " + l + " hPa"]; });
    o.push(["rain", "Rain"], ["rh", "Relative humidity " + M.rhLevel + " hPa"]);
    if (cddOf(model)) o.push(["cdd", "Consecutive dry days"]);
    return o;
  }
  function rebuildShadeOptions() {
    var opts = shadeOptionsFor(state.model);
    shadeSel.innerHTML = "";
    opts.forEach(function (o) { shadeSel.add(new Option(o[1], o[0])); });
    if (!opts.some(function (o) { return o[0] === state.shade; })) state.shade = opts[0][0];
    shadeSel.value = state.shade;
  }

  function setModel(key) {
    var prevModel = state.model;
    var prevT = M.models[prevModel].valid[state.t];
    var prevDay = inCdd() && state.day != null ? dayMs(cddOf(prevModel).dates[state.day]) : null;
    var wasCdd = inCdd();
    if (shadeGroup(prevModel) !== shadeGroup(key)) {
      state.lastShade[shadeGroup(prevModel)] = state.shade;
      if (state.lastShade[shadeGroup(key)]) state.shade = state.lastShade[shadeGroup(key)];
    }
    state.model = key;
    var d = M.models[key];
    if (wasCdd && !cddOf(key) && shadeGroup(key) === "nwp") state.shade = state.prevShade || "speed" + M.levels[0];
    rebuildShadeOptions();
    if (wasCdd && !inCdd() && shadeGroup(key) === "nwp") {     // this model has no dry-day data (its analysis failed)
      setStatus("No consecutive-dry-days layer for " + d.label);
      setTimeout(function () { setStatus(""); }, 2500);
    }
    state.t = nearest(d.valid, prevT);
    if (cddOf(key) && prevDay != null) state.day = nearest(cddOf(key).dates.map(dayMs), prevDay);
    document.querySelectorAll("#wv-tabs button").forEach(function (b) {
      b.setAttribute("aria-selected", String(b.dataset.key === key));
    });
    syncControls();
    setFrame(axisIdx());
  }

  // ---- EPS tab: the image is loaded by the visitor's browser straight from ECMWF Open Charts ----------------------------
  var EPS = { place: "Singapore", lat: 1.28967, lon: 103.85, epsgram: "classical_10d",
              api: "https://charts.ecmwf.int/opencharts-api/v1/products/opencharts_meteogram/",
              page: "https://charts.ecmwf.int/products/opencharts_meteogram" };
  var epsStarted = false;

  function epsBaseTimes(n) {                // newest 00/12 UTC first
    var d = new Date(); d.setUTCMinutes(0, 0, 0); d.setUTCHours(d.getUTCHours() - d.getUTCHours() % 12);
    var out = [];
    for (var i = 0; i < n; i++) out.push(new Date(d.getTime() - i * 12 * 3600e3));
    return out;
  }
  function baseStr(d) { return d.toISOString().slice(0, 16).replace(/[-:T]/g, ""); }     // YYYYMMDDHHMM
  function epsQuery(d) {
    return "epsgram=" + EPS.epsgram + "&base_time=" + baseStr(d) + "&lat=" + EPS.lat + "&lon=" + EPS.lon +
      "&station_name=" + encodeURIComponent(EPS.place);
  }
  function epsPage(d) { return EPS.page + "?" + "base_time=" + baseStr(d) + "&epsgram=" + EPS.epsgram +
    "&lat=" + EPS.lat + "&lon=" + EPS.lon + "&station_name=" + encodeURIComponent(EPS.place); }

  function findImageUrl(doc) {              // data.link.href, else any image-looking URL in the answer
    var data = doc && doc.data ? doc.data : doc;
    if (data && data.link && typeof data.link.href === "string") return new URL(data.link.href, EPS.api).href;
    var found = null;
    (function walk(o) {
      if (found || o == null) return;
      if (typeof o === "string") { if (/^(https?:)?\/\/|^\//.test(o) && /\.(png|jpe?g|svg|webp)(\?|$)/i.test(o)) found = new URL(o, EPS.api).href; }
      else if (typeof o === "object") Object.keys(o).forEach(function (k) { walk(o[k]); });
    })(doc);
    return found;
  }
  function preload(url) {                   // <img> needs no CORS; resolves once the picture really loads
    return new Promise(function (resolve, reject) {
      var im = new Image();
      im.onload = function () { resolve(url); };
      im.onerror = function () { reject(new Error("image did not load")); };
      im.src = url;
    });
  }
  function epsShow(url, base) {
    var img = el("wv-eps-img");
    img.src = url; img.hidden = false;
    el("wv-eps-full").href = url; el("wv-eps-page").href = epsPage(base);
    el("wv-eps-state").textContent = "";
    var when = fmt(base.getTime() / 1000, { weekday: "short", day: "2-digit", month: "short", hour: "2-digit" }) + " UTC";
    el("wv-eps-sub").textContent = "ECMWF ensemble (ENS) forecast from " + when + " (base time), the latest available";
    el("wv-run").textContent = "10-day EPSgram \u00b7 base time " + when;
  }
  function epsFallback(cands, why) {        // cannot show the picture here: link to the same chart on ECMWF
    var box = el("wv-eps-state");
    box.innerHTML = "The chart could not be loaded into this page (" + why + "). Open it on ECMWF Open Charts: ";
    cands.slice(0, 2).forEach(function (d, i) {
      var a = document.createElement("a");
      a.href = epsPage(d); a.target = "_blank"; a.rel = "noopener";
      a.textContent = fmt(d.getTime() / 1000, { day: "2-digit", month: "short", hour: "2-digit" }) + " UTC run";
      box.appendChild(a); if (i === 0) box.appendChild(document.createTextNode(" \u00b7 "));
    });
  }
  function epsLoad() {
    var cands = epsBaseTimes(4);
    el("wv-eps-page").href = epsPage(cands[0]);
    el("wv-eps-state").textContent = "Loading the latest chart from ECMWF\u2026";
    function attempt(i) {
      if (i >= cands.length) return epsFallback(cands, "no chart found for the last " + cands.length + " runs");
      var d = cands[i];
      fetch(EPS.api + "?" + epsQuery(d)).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        if ((r.headers.get("content-type") || "").indexOf("image/") === 0) return r.blob().then(URL.createObjectURL);
        return r.json().then(function (doc) {
          var u = findImageUrl(doc);
          if (!u) throw new Error("no image link in the answer");
          return u;
        });
      }).then(preload).then(function (url) { epsShow(url, d); })
        .catch(function (err) {
          if (err instanceof TypeError) return epsFallback(cands, "the browser blocked the request to ECMWF");
          attempt(i + 1);                   // not published yet (or an error): try the previous run
        });
    }
    attempt(0);
  }

  function showEps() {
    stop();
    state.view = "eps";
    document.body.classList.add("wv-eps-view");
    if (!epsStarted) { epsStarted = true; epsLoad(); }
    el("wv-eps-title").textContent = EPS.place + " \u00b7 10-day ensemble meteogram (EPSgram)";
    el("wv-valid").textContent = "Ensemble meteogram \u00b7 " + EPS.place;
    if (!el("wv-eps-img").getAttribute("src")) el("wv-run").textContent = "10-day EPSgram \u00b7 loading\u2026";
    document.querySelectorAll("#wv-tabs button").forEach(function (b) {
      b.setAttribute("aria-selected", String(b.dataset.key === "eps"));
    });
  }
  function showMap() {
    if (state.view === "map") return;
    state.view = "map";
    document.body.classList.remove("wv-eps-view");
  }

  // ---- controls -----------------------------------------------------------------------------------------------------------
  models.forEach(function (key) {
    var b = document.createElement("button");
    b.type = "button"; b.setAttribute("role", "tab"); b.dataset.key = key;
    b.textContent = M.models[key].label;
    b.addEventListener("click", function () { showMap(); setModel(key); });
    el("wv-tabs").appendChild(b);
  });
  var eb = document.createElement("button");   // separate tab: the ECMWF ensemble meteogram image
  eb.type = "button"; eb.setAttribute("role", "tab"); eb.dataset.key = "eps";
  eb.textContent = "EPS \u00b7 " + EPS.place;
  eb.addEventListener("click", showEps);
  el("wv-tabs").appendChild(eb);

  var shadeSel = el("wv-shade"), levelSel = el("wv-level");
  M.levels.forEach(function (l) { levelSel.add(new Option(l + " hPa", String(l))); });
  rebuildShadeOptions();
  levelSel.value = String(state.level);
  shadeSel.addEventListener("change", function () {
    var was = inCdd();
    if (!was) state.prevShade = state.shade;
    state.shade = shadeSel.value;
    if (inCdd() && !cddOf(state.model)) {   // option should be disabled, but be safe
      state.shade = state.prevShade; shadeSel.value = state.shade;
      return;
    }
    if (inCdd() && (state.day == null || state.day >= axisLen())) state.day = axisLen() - 1;   // start on the full-forecast maximum
    syncControls();
    refresh();
  });
  levelSel.addEventListener("change", function () { state.level = +levelSel.value; refresh(); });
  document.querySelectorAll("#wv-style button").forEach(function (b) {
    b.addEventListener("click", function () {
      state.style = b.dataset.style;
      document.querySelectorAll("#wv-style button").forEach(function (o) {
        o.setAttribute("aria-pressed", String(o === b));
      });
      refresh();
    });
  });
  document.querySelectorAll("#wv-cdd-controls input").forEach(function (b) {
    b.addEventListener("change", syncOutlines);
  });

  slider.addEventListener("input", function () { setFrame(+slider.value); });
  function stop() {
    clearInterval(timer); timer = null;
    el("wv-play").innerHTML = "&#9654;"; el("wv-play").setAttribute("aria-label", "Play");
  }
  function play() {
    el("wv-play").innerHTML = "&#10074;&#10074;"; el("wv-play").setAttribute("aria-label", "Pause");
    timer = setInterval(function () {
      var n = axisLen();
      setFrame(axisIdx() + 1 >= n ? 0 : axisIdx() + 1);
    }, PLAY_MS);
  }
  el("wv-play").addEventListener("click", function () { timer ? stop() : play(); });
  document.addEventListener("keydown", function (e) {
    if (state.view === "eps" || e.target === slider || e.target.tagName === "BUTTON" || e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
    if (e.key === "ArrowRight") setFrame(axisIdx() + 1);
    if (e.key === "ArrowLeft") setFrame(axisIdx() - 1);
  });

  setModel(state.model);
});
