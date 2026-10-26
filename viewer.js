// folium emits this block before it creates the map, so wait for page load
window.addEventListener("load", function () {
  var map = __MAP__;
  var DATA = __DATA__;
  var GRID = __GRID__;
  var COLORS = __COLORS__;
  var EDGES = __EDGES__;

  var MIN_ARROW_PX = 22;                 // thin the arrows out below this spacing
  var PLAY_MS = 450;

  var el = function (id) { return document.getElementById(id); };
  var keys = Object.keys(DATA);
  var model = keys[0];
  var idx = 0;
  var timer = null;

  // ---- UI chrome must not pan/zoom the map -------------------------------
  ["wv-top", "wv-bottom", "wv-legend"].forEach(function (id) {
    L.DomEvent.disableClickPropagation(el(id));
    L.DomEvent.disableScrollPropagation(el(id));
  });

  // ---- speed raster -------------------------------------------------------
  var overlay = L.imageOverlay(DATA[model].frames[0].img,
    GRID.bounds, { interactive: false, opacity: 1 }).addTo(map);

  // ---- arrow canvas -------------------------------------------------------
  var canvas = document.createElement("canvas");
  canvas.className = "wv-arrows";
  canvas.style.zIndex = 450;
  map.getContainer().appendChild(canvas);
  var drawQueued = false;

  function decode(frame) {
    if (frame._uv) return frame._uv;
    var bin = atob(frame.uv), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    frame._uv = new Int16Array(bytes.buffer);
    return frame._uv;
  }

  function arrowPath(x, y, u, v) {
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

  function draw() {
    drawQueued = false;
    var size = map.getSize(), dpr = window.devicePixelRatio || 1;
    if (canvas.width !== size.x * dpr || canvas.height !== size.y * dpr) {
      canvas.width = size.x * dpr; canvas.height = size.y * dpr;
      canvas.style.width = size.x + "px"; canvas.style.height = size.y + "px";
    }
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);
    ctx.lineCap = "round"; ctx.lineJoin = "round";

    var uv = decode(DATA[model].frames[idx]);
    var n = GRID.nlat * GRID.nlon;
    var pxPerStep = GRID.d * 256 * Math.pow(2, map.getZoom()) / 360;
    var k = Math.max(1, Math.ceil(MIN_ARROW_PX / pxPerStep));
    var paths = [];
    for (var i = 0; i < GRID.nlat; i += k) {
      var lat = GRID.lat0 - i * GRID.d;
      for (var j = 0; j < GRID.nlon; j += k) {
        var lon = GRID.lon0 + j * GRID.d;
        var pt = map.latLngToContainerPoint([lat, lon]);
        if (pt.x < -20 || pt.y < -20 || pt.x > size.x + 20 || pt.y > size.y + 20) continue;
        var p = arrowPath(pt.x, pt.y, uv[i * GRID.nlon + j] / 10, uv[n + i * GRID.nlon + j] / 10);
        if (p) paths.push(p);
      }
    }
    ctx.strokeStyle = "rgba(255,255,255,0.85)"; ctx.lineWidth = 3.4;
    paths.forEach(function (p) { ctx.stroke(p); });
    ctx.strokeStyle = "#1a1a19"; ctx.lineWidth = 1.3;
    paths.forEach(function (p) { ctx.stroke(p); });
  }

  function scheduleDraw() {
    if (!drawQueued) { drawQueued = true; requestAnimationFrame(draw); }
  }
  map.on("move resize zoomend", scheduleDraw);
  map.on("zoomstart", function () { canvas.style.visibility = "hidden"; });
  map.on("zoomend", function () { canvas.style.visibility = "visible"; });

  // ---- time labels --------------------------------------------------------
  function fmt(sec, opts) {
    return new Date(sec * 1000).toLocaleString("en-GB",
      Object.assign({ timeZone: "UTC", hour12: false }, opts));
  }
  function validLabel(f) {
    return fmt(f.valid, { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
      .replace(",", "") + " UTC · +" + f.step + " h";
  }
  function shortLabel(f) {
    return fmt(f.valid, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
      .replace(",", "") + "Z";
  }

  // ---- frame / model switching -------------------------------------------
  var slider = el("wv-slider");

  function setFrame(i) {
    var frames = DATA[model].frames;
    idx = Math.max(0, Math.min(frames.length - 1, i));
    var f = frames[idx];
    overlay.setUrl(f.img);
    slider.value = idx;
    el("wv-valid").textContent = validLabel(f);
    scheduleDraw();
  }

  function setModel(key) {
    var prev = DATA[model].frames[idx];
    model = key;
    var d = DATA[key], frames = d.frames, best = 0;
    frames.forEach(function (f, i) {
      if (Math.abs(f.valid - prev.valid) < Math.abs(frames[best].valid - prev.valid)) best = i;
    });
    frames.forEach(function (f) {          // warm the image cache so playback does not flicker
      if (!f._im) { f._im = new Image(); f._im.src = f.img; }
    });
    slider.max = frames.length - 1;
    el("wv-run").textContent = d.long + " · run " + fmt(d.run, { day: "2-digit", month: "short", hour: "2-digit" }).replace(",", "") + " UTC";
    el("wv-first").textContent = shortLabel(frames[0]);
    el("wv-last").textContent = shortLabel(frames[frames.length - 1]);
    document.querySelectorAll("#wv-tabs button").forEach(function (b) {
      b.setAttribute("aria-selected", String(b.dataset.key === key));
    });
    setFrame(best);
  }

  keys.forEach(function (key) {
    var b = document.createElement("button");
    b.type = "button"; b.setAttribute("role", "tab"); b.dataset.key = key;
    b.textContent = DATA[key].label;
    b.addEventListener("click", function () { setModel(key); });
    el("wv-tabs").appendChild(b);
  });

  slider.addEventListener("input", function () { setFrame(+slider.value); });

  function stop() {
    clearInterval(timer); timer = null;
    el("wv-play").innerHTML = "&#9654;"; el("wv-play").setAttribute("aria-label", "Play");
  }
  function play() {
    el("wv-play").innerHTML = "&#10074;&#10074;"; el("wv-play").setAttribute("aria-label", "Pause");
    timer = setInterval(function () {
      setFrame(idx + 1 >= DATA[model].frames.length ? 0 : idx + 1);
    }, PLAY_MS);
  }
  el("wv-play").addEventListener("click", function () { timer ? stop() : play(); });
  document.addEventListener("keydown", function (e) {
    if (e.target === slider || e.target.tagName === "BUTTON") return;
    if (e.key === "ArrowRight") setFrame(idx + 1);
    if (e.key === "ArrowLeft") setFrame(idx - 1);
  });

  // ---- legend (discrete 5-knot bins, last swatch = over-range) --------------
  (function () {
    var bar = el("wv-legend-bar"), n = COLORS.length;
    COLORS.forEach(function (c) {
      var d = document.createElement("span");
      d.style.background = c;
      bar.appendChild(d);
    });
    EDGES.forEach(function (v, i) {
      var t = document.createElement("span");
      t.textContent = v;
      t.style.left = ((i + 1) / n * 100) + "%";
      el("wv-legend-ticks").appendChild(t);
    });
  })();

  setModel(model);
});
