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

  var el = function (id) { return document.getElementById(id); };
  var models = Object.keys(M.models);
  var state = { model: models[0], t: 0, shade: "speed" + M.levels[0], level: M.levels[0], style: "arrows" };
  var timer = null;

  ["wv-top", "wv-bottom", "wv-legend"].forEach(function (id) {
    L.DomEvent.disableClickPropagation(el(id));
    L.DomEvent.disableScrollPropagation(el(id));
  });

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

  function shadeKind() { return state.shade.indexOf("speed") === 0 ? "speed" : state.shade === "rain" ? "rain" : "rh"; }
  function shadeFile() { return state.shade === "rh" ? "rh" + M.rhLevel : state.shade; }
  function neededFiles() {
    var f = [shadeFile().replace(/^speed/, "uv")];
    if (state.style !== "off") f.push("uv" + state.level);
    return f.filter(function (n, i) { return f.indexOf(n) === i; });
  }
  function get(name) { return arrays[state.model + "/" + name]; }

  // ---- colour lookup tables ---------------------------------------------------------------------
  var LUTS = {};
  function lut(kind) {
    if (LUTS[kind]) return LUTS[kind];
    var P = M.palettes[kind], vmax = { speed: 150, rain: 300, rh: 100 }[kind], scale = { speed: 4, rain: 10, rh: 4 }[kind];
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
  var flowLayer = canvasLayer("wv-flow", 450);

  fetch("data/coastlines.json").then(function (r) { return r.json(); }).then(function (gj) {
    map.createPane("wv-coast");
    map.getPane("wv-coast").style.zIndex = 420;
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
  var field = new Float32Array(N);
  var shadeReady = false;

  function fillField() {                    // shading variable in display units for the current frame
    var kind = shadeKind(), t = state.t, i;
    if (kind === "speed") {
      var uv = get(shadeFile().replace(/^speed/, "uv")), b = t * 2 * N, k = 0.1 * KT;
      for (i = 0; i < N; i++) field[i] = Math.hypot(uv[b + i], uv[b + N + i]) * k;
    } else {
      var a = get(shadeFile()), s = kind === "rain" ? 0.1 : 1, o = t * N;
      for (i = 0; i < N; i++) field[i] = a[o + i] * s;
    }
  }

  function renderShade() {
    var L_ = lut(shadeKind()), T = L_.t, sc = L_.scale, nmax = L_.n - 1;
    fillField();
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
    shadeReady = true;
  }

  function drawShade() {
    var ctx = shadeLayer.begin();
    if (!shadeReady) return;
    var nw = map.latLngToContainerPoint([LAT_N, LON_W]), se = map.latLngToContainerPoint([LAT_S, LON_E]);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(off, nw.x, nw.y, se.x - nw.x, se.y - nw.y);
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
    var size = map.getSize(), b = state.t * 2 * N;
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
    var uv = get("uv" + state.level);
    if (!uv) { streams = null; return; }
    var size = map.getSize(), z = map.getZoom(), world = 256 * Math.pow(2, z), org = map.getPixelOrigin();
    var tl = map.containerPointToLayerPoint([0, 0]);
    var x0 = tl.x - MARGIN, y0 = tl.y - MARGIN, W = size.x + 2 * MARGIN, H = size.y + 2 * MARGIN;
    var cols = Math.ceil(W / CELL), rows = Math.ceil(H / CELL), occ = new Uint8Array(cols * rows);
    var base = state.t * 2 * N;

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
    if (state.style === "off") return;
    var uv = get("uv" + state.level);
    if (!uv) return;
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
      if (q.streams) { q.streams = false; if (state.style === "streams") computeStreams(); }
      drawShade(); drawFlow();
    });
  }
  map.on("move resize", function () { schedule(false); });
  map.on("moveend zoomend", function () { schedule(true); });
  map.on("zoomstart", function () { shadeLayer.canvas.style.visibility = flowLayer.canvas.style.visibility = "hidden"; });
  map.on("zoomend", function () { shadeLayer.canvas.style.visibility = flowLayer.canvas.style.visibility = "visible"; });

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
    var iv = M.models[state.model].interval[state.t];
    return iv ? "Rain in the preceding " + iv + " h (mm)" : "Rain (none at +0 h)";
  }
  function drawLegend() {
    var P = M.palettes[shadeKind()], bar = el("wv-legend-bar"), ticks = el("wv-legend-ticks");
    el("wv-legend-title").textContent = legendTitle();
    if (bar.dataset.kind !== shadeKind()) {
      bar.dataset.kind = shadeKind();
      bar.innerHTML = ""; ticks.innerHTML = "";
      P.colors.forEach(function (c) {
        var d = document.createElement("span"); d.style.background = c; bar.appendChild(d);
      });
      P.edges.forEach(function (v, i) {
        var t = document.createElement("span");
        t.textContent = v; t.style.left = ((i + 1) / P.colors.length * 100) + "%";
        ticks.appendChild(t);
      });
    }
  }

  var ro = el("wv-readout");
  map.on("mousemove", function (e) {
    var shadeArr = get(shadeFile().replace(/^speed/, "uv")) || get(shadeFile());
    var parts = [Math.abs(e.latlng.lat).toFixed(1) + "°" + (e.latlng.lat < 0 ? "S" : "N") + " " +
      e.latlng.lng.toFixed(1) + "°E"];
    var uv = get("uv" + state.level);
    if (uv && state.style !== "off" && sampleUV(uv, state.t * 2 * N, e.latlng.lng, e.latlng.lat)) {
      var kt = Math.hypot(su, sv) * 0.1 * KT, from = (270 - Math.atan2(sv, su) / RAD + 360) % 360;
      parts.push(state.level + " hPa wind " + kt.toFixed(0) + " kt from " + String(Math.round(from) % 360).padStart(3, "0") + "°");
    }
    if (shadeArr && shadeReady && state.shade !== "speed" + state.level) {
      var gx = (e.latlng.lng - LON_W) / G.d, gy = (LAT_N - e.latlng.lat) / G.d;
      if (gx >= 0 && gy >= 0 && gx <= NX - 1 && gy <= NY - 1) {
        var v = field[Math.round(gy) * NX + Math.round(gx)];
        parts.push(state.shade.indexOf("speed") === 0 ? state.shade.replace("speed", "") + " hPa wind " + v.toFixed(0) + " kt" :
          state.shade === "rain" ? v.toFixed(1) + " mm" : "RH " + v.toFixed(0) + "%");
      }
    }
    ro.textContent = parts.join(" · "); ro.hidden = false;
  });
  map.on("mouseout", function () { ro.hidden = true; });

  // ---- state -> screen -----------------------------------------------------------------------------------------------
  var slider = el("wv-slider"), status = el("wv-status"), token = 0;

  function setStatus(msg) { status.textContent = msg || ""; status.hidden = !msg; }

  function refresh() {                      // make sure the needed files are loaded, then render
    var names = neededFiles(), tk = ++token;
    drawLegend();
    el("wv-valid").textContent = validLabel(state.model, state.t);
    slider.value = state.t;
    var missing = names.filter(function (n) { return !get(n); });
    if (!missing.length) return render();
    setStatus("Loading…");
    Promise.all(missing.map(function (n) { return load(state.model, n); })).then(function () {
      if (tk !== token) return;
      setStatus(""); render();
    }).catch(function (err) { if (tk === token) setStatus("Could not load data: " + err.message); });
  }

  function render() {
    renderShade();
    schedule(true);
  }

  function setFrame(t) {
    state.t = Math.max(0, Math.min(M.models[state.model].steps.length - 1, t));
    refresh();
  }

  function setModel(key) {
    var prev = M.models[state.model].valid[state.t];
    state.model = key;
    var d = M.models[key], best = 0;
    d.valid.forEach(function (v, i) { if (Math.abs(v - prev) < Math.abs(d.valid[best] - prev)) best = i; });
    slider.max = d.steps.length - 1;
    el("wv-run").textContent = d.long + " · run " + fmt(d.run, { day: "2-digit", month: "short", hour: "2-digit" }) + " UTC";
    el("wv-first").textContent = shortLabel(key, 0);
    el("wv-last").textContent = shortLabel(key, d.steps.length - 1);
    document.querySelectorAll("#wv-tabs button").forEach(function (b) {
      b.setAttribute("aria-selected", String(b.dataset.key === key));
    });
    setFrame(best);
  }

  // ---- controls -----------------------------------------------------------------------------------------------------------
  models.forEach(function (key) {
    var b = document.createElement("button");
    b.type = "button"; b.setAttribute("role", "tab"); b.dataset.key = key;
    b.textContent = M.models[key].label;
    b.addEventListener("click", function () { setModel(key); });
    el("wv-tabs").appendChild(b);
  });

  var shadeSel = el("wv-shade"), levelSel = el("wv-level");
  M.levels.forEach(function (l) {
    shadeSel.add(new Option("Wind speed " + l + " hPa", "speed" + l));
    levelSel.add(new Option(l + " hPa", String(l)));
  });
  shadeSel.add(new Option("Rain", "rain"));
  shadeSel.add(new Option("Relative humidity " + M.rhLevel + " hPa", "rh"));
  shadeSel.value = state.shade; levelSel.value = String(state.level);
  shadeSel.addEventListener("change", function () { state.shade = shadeSel.value; refresh(); });
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

  slider.addEventListener("input", function () { setFrame(+slider.value); });
  function stop() {
    clearInterval(timer); timer = null;
    el("wv-play").innerHTML = "&#9654;"; el("wv-play").setAttribute("aria-label", "Play");
  }
  function play() {
    el("wv-play").innerHTML = "&#10074;&#10074;"; el("wv-play").setAttribute("aria-label", "Pause");
    timer = setInterval(function () {
      var n = M.models[state.model].steps.length;
      setFrame(state.t + 1 >= n ? 0 : state.t + 1);
    }, PLAY_MS);
  }
  el("wv-play").addEventListener("click", function () { timer ? stop() : play(); });
  document.addEventListener("keydown", function (e) {
    if (e.target === slider || e.target.tagName === "BUTTON" || e.target.tagName === "SELECT") return;
    if (e.key === "ArrowRight") setFrame(state.t + 1);
    if (e.key === "ArrowLeft") setFrame(state.t - 1);
  });

  setModel(state.model);
});
