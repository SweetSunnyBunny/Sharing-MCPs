
(function () {
  "use strict";

  const API = "/api/coloring";
  const UNDO_LIMIT = 20;
  const SIZES = [8, 18, 36];          // brush diameters in SCREEN pixels: small, medium, big
  const CANCEL_IF_PINCH_WITHIN_MS = 350;
  const BLEND_REACH = 0.6, BLEND_SPACING = 0.3, BLEND_PICKUP = 0.3, BLEND_DEPOSIT = 0.35;

  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  async function getJSON(url) {
    const r = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("image " + src));
      img.src = src;
    });
  }
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  // ── the drawer ──────────────────────────────────────────────────────────
  function mountDrawer(root) {
    let palette = null;

    async function render() {
      root.innerHTML = "";
      const head = h("div", "cd-head");
      head.appendChild(h("h1", "cd-title", "Colouring drawer"));
      head.appendChild(h("div", "cd-sub", "Pick a page. Your colour goes under the lines."));
      root.appendChild(head);
      let pages = [];
      try {
        pages = (await getJSON(API + "/pages")).pages || [];
      } catch (e) {
        root.appendChild(h("div", "cd-quiet", "The drawer didn't open just now. Tap Colouring again in a moment."));
        return;
      }
      if (!pages.length) {
        root.appendChild(h("div", "cd-quiet", "The drawer is waiting for its first page."));
        return;
      }
      const grid = h("div", "cd-grid");
      const bust = Date.now();
      for (const p of pages) {
        const card = h("button", "cd-card");
        card.type = "button";
        const thumb = h("div", "cd-thumb");
        if (p.started) {
          const paint = h("img"); paint.alt = ""; paint.loading = "lazy"; paint.src = p.paint_url + "?t=" + bust;
          thumb.appendChild(paint);
        }
        const lines = h("img"); lines.alt = p.title; lines.loading = "lazy"; lines.src = p.lines_url;
        thumb.appendChild(lines);
        card.appendChild(thumb);
        card.appendChild(h("div", "cd-name", p.title));
        card.appendChild(h("div", "cd-from", "from " + (p.drawn_by || p.dropped_by)));
        card.addEventListener("click", async () => {
          if (!palette) palette = await getJSON(API + "/palette").catch(() => null);
          openBoard(p, palette, render);
        });
        grid.appendChild(card);
      }
      root.appendChild(grid);

      let shelf = [];
      try { shelf = (await getJSON(API + "/shelf")).pages || []; } catch (e) { shelf = []; }
      if (!shelf.length) return;          // no empty shelf, no "0 finished"; it appears with her first one
      const sh = h("div", "cs-head");
      sh.appendChild(h("h2", "cs-title", "My shelf"));
      sh.appendChild(h("div", "cs-sub", "Pages you finished. Tap one to watch yourself colour it again."));
      root.appendChild(sh);
      const row = h("div", "cs-shelf");
      for (const p of shelf) {
        const card = h("button", "cs-print");
        card.type = "button";
        const thumb = h("div", "cd-thumb");
        if (p.started) {
          const paint = h("img"); paint.alt = ""; paint.loading = "lazy"; paint.src = p.paint_url + "?t=" + bust;
          thumb.appendChild(paint);
        }
        const lines = h("img"); lines.alt = p.title; lines.loading = "lazy"; lines.src = p.lines_url;
        thumb.appendChild(lines);
        card.appendChild(thumb);
        card.appendChild(h("div", "cs-name", p.title));
        card.appendChild(h("div", "cs-colophon", p.colophon || ""));
        card.addEventListener("click", () => openTimelapse(p, async () => {
          if (!palette) palette = await getJSON(API + "/palette").catch(() => null);
          openBoard(p, palette, render);
        }));
        row.appendChild(card);
      }
      root.appendChild(row);
    }
    render();
  }

  function blendDabOn(ctx, W, H, cx, cy, r, state, pickupRate, deposit) {
    r = Math.max(1.5, r);
    const x0 = clamp(Math.floor(cx - r), 0, W), y0 = clamp(Math.floor(cy - r), 0, H);
    const x1 = clamp(Math.ceil(cx + r), 0, W), y1 = clamp(Math.ceil(cy + r), 0, H);
    const bw = x1 - x0, bh = y1 - y0;
    if (bw <= 0 || bh <= 0) return;
    const img = ctx.getImageData(x0, y0, bw, bh), d = img.data;
    const r2 = r * r;
    const fall = new Float32Array(bw * bh);
    let sr = 0, sg = 0, sb = 0, sa = 0, sw = 0;
    for (let j = 0; j < bh; j++) {
      const dy = y0 + j + 0.5 - cy;
      for (let i = 0; i < bw; i++) {
        const dx = x0 + i + 0.5 - cx;
        const q = 1 - (dx * dx + dy * dy) / r2;
        if (q <= 0) continue;
        const f = q * q;
        const n = j * bw + i, k = n * 4, a = d[k + 3] / 255;
        fall[n] = f;
        sr += d[k] * a * f; sg += d[k + 1] * a * f; sb += d[k + 2] * a * f; sa += a * f; sw += f;
      }
    }
    if (!sw) return;
    const s = [sr / sw, sg / sw, sb / sw, sa / sw];
    const pk = state.pickup = state.pickup ? state.pickup.map((v, n) => v + (s[n] - v) * pickupRate) : s;
    for (let n = 0; n < fall.length; n++) {
      const f = fall[n];
      if (!f) continue;
      const m = deposit * f, k = n * 4, a = d[k + 3] / 255;
      const pa = a + (pk[3] - a) * m;
      if (pa < 0.004) { d[k + 3] = 0; continue; }
      d[k]     = (d[k] * a     + (pk[0] - d[k] * a) * m) / pa;
      d[k + 1] = (d[k + 1] * a + (pk[1] - d[k + 1] * a) * m) / pa;
      d[k + 2] = (d[k + 2] * a + (pk[2] - d[k + 2] * a) * m) / pa;
      d[k + 3] = pa * 255;
    }
    ctx.putImageData(img, x0, y0);
  }

  function makeReplayer(ctx, sctx, W, H) {
    let undo = [], redo = [], session = null;
    function rectOf(ev) {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const p of ev.pts) { minX = Math.min(minX, p[0]); minY = Math.min(minY, p[1]); maxX = Math.max(maxX, p[0]); maxY = Math.max(maxY, p[1]); }
      const pad = Math.ceil((ev.w || 1) * 1.3) + 2;
      const x = clamp(Math.floor(minX - pad), 0, W), y = clamp(Math.floor(minY - pad), 0, H);
      const x2 = clamp(Math.ceil(maxX + pad), 0, W), y2 = clamp(Math.ceil(maxY + pad), 0, H);
      return { x, y, w: Math.max(1, x2 - x), h: Math.max(1, y2 - y) };
    }
    const snap = r => ({ rect: r, data: ctx.getImageData(r.x, r.y, r.w, r.h) });
    function path(c, ev, color) {
      const pts = ev.pts;
      c.fillStyle = color; c.strokeStyle = color; c.lineCap = "round"; c.lineJoin = "round";
      c.beginPath(); c.arc(pts[0][0], pts[0][1], (pts[0][3] || ev.w) / 2, 0, Math.PI * 2); c.fill();
      for (let i = 1; i < pts.length; i++) {
        c.lineWidth = pts[i][3] || ev.w;
        c.beginPath(); c.moveTo(pts[i - 1][0], pts[i - 1][1]); c.lineTo(pts[i][0], pts[i][1]); c.stroke();
      }
    }
    function stroke(ev) {
      if (ev.session !== session) { session = ev.session; undo = []; redo = []; }
      if (!Array.isArray(ev.pts) || !ev.pts.length) return;
      const r = rectOf(ev);
      undo.push(snap(r)); if (undo.length > UNDO_LIMIT) undo.shift(); redo.length = 0;
      if (ev.tool === "blend") {
        const b = ev.blend || { reach: BLEND_REACH, spacing: BLEND_SPACING, pickup: BLEND_PICKUP, deposit: BLEND_DEPOSIT };
        const state = { pickup: null };
        const p0 = ev.pts[0];
        blendDabOn(ctx, W, H, p0[0], p0[1], (p0[3] || ev.w) * b.reach, state, b.pickup, b.deposit);
        for (let i = 1; i < ev.pts.length; i++) {
          const from = ev.pts[i - 1], to = ev.pts[i], rr = (to[3] || ev.w) * b.reach;
          const step = Math.max(0.75, rr * b.spacing);
          const n = Math.max(1, Math.ceil(Math.hypot(to[0] - from[0], to[1] - from[1]) / step));
          for (let k = 1; k <= n; k++) {
            const t = k / n;
            blendDabOn(ctx, W, H, from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t, rr, state, b.pickup, b.deposit);
          }
        }
      } else if (ev.tool === "eraser") {
        ctx.save(); ctx.globalCompositeOperation = "destination-out"; path(ctx, ev, "#000"); ctx.restore();
      } else {
        // the board draws a crayon stroke at full strength on its live layer, then lays it down softly
        sctx.clearRect(r.x, r.y, r.w, r.h);
        path(sctx, ev, ev.color || "#F4A6BC");
        ctx.save(); ctx.globalAlpha = ev.opacity == null ? 0.9 : ev.opacity;
        ctx.drawImage(sctx.canvas, r.x, r.y, r.w, r.h, r.x, r.y, r.w, r.h);
        ctx.restore();
      }
    }
    function swap(from, to) {
      const step = from.pop();
      if (!step) return;
      const r = step.rect;
      to.push(snap(r));
      ctx.putImageData(step.data, r.x, r.y);
    }
    return {
      reset() { undo = []; redo = []; session = null; ctx.clearRect(0, 0, W, H); },
      apply(ev) {
        if (!ev || typeof ev !== "object") return;
        if (ev.type === "stroke") stroke(ev);
        else if (ev.type === "undo") swap(undo, redo);
        else if (ev.type === "redo") swap(redo, undo);
        else if (ev.type === "clear") { ctx.clearRect(0, 0, W, H); undo = []; redo = []; }
      },
    };
  }

  async function openTimelapse(page, onColourMore) {
    const W = page.width, H = page.height;
    document.body.style.overflow = "hidden";
    const wrap = h("div", "ct");
    const stage = h("div", "ct-stage");
    const board = h("div", "ct-board");
    const paint = document.createElement("canvas"); paint.width = W; paint.height = H;
    const lines = h("img"); lines.alt = page.title; lines.draggable = false;
    board.appendChild(paint); board.appendChild(lines);
    stage.appendChild(board);
    const col = h("div", "ct-colophon", page.colophon || "");
    const bar = h("div", "ct-bar");
    const closeBtn = h("button", "cb-btn", "‹ Shelf");
    const againBtn = h("button", "cb-btn", "Watch again");
    const moreBtn = h("button", "cb-btn", "Colour more");
    [closeBtn, againBtn, moreBtn].forEach(b => { b.type = "button"; bar.appendChild(b); });
    wrap.appendChild(stage); wrap.appendChild(col); wrap.appendChild(bar);
    document.body.appendChild(wrap);

    function fit() {
      const r = stage.getBoundingClientRect();
      const s = Math.min((r.width - 24) / W, (r.height - 24) / H);
      board.style.width = Math.max(1, W * s) + "px";
      board.style.height = Math.max(1, H * s) + "px";
    }
    fit();
    window.addEventListener("resize", fit);

    const ctx = paint.getContext("2d");
    const scratch = document.createElement("canvas"); scratch.width = W; scratch.height = H;
    const player = makeReplayer(ctx, scratch.getContext("2d"), W, H);
    let raf = 0, closed = false;

    function close() {
      closed = true;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", fit);
      document.body.style.overflow = "";
      wrap.remove();
    }
    closeBtn.addEventListener("click", close);
    moreBtn.addEventListener("click", () => { close(); onColourMore && onColourMore(); });

    let events = [], print = null;
    try {
      const [, data, img] = await Promise.all([
        loadImage(page.lines_url).then(img => { lines.src = img.src; }),
        getJSON(page.strokes_url || `${API}/page/${encodeURIComponent(page.id)}/strokes`),
        page.started ? loadImage(page.paint_url + "?t=" + Date.now()).catch(() => null) : Promise.resolve(null),
      ]);
      events = (data && data.events) || [];
      print = img;
    } catch (e) {
      col.textContent = "The replay didn't load just now. Your page is safe on the shelf.";
      return;
    }

    function endOnPrint() {
      if (print) { ctx.clearRect(0, 0, W, H); ctx.drawImage(print, 0, 0, W, H); }
      againBtn.disabled = false;
    }

    function play() {
      if (closed) return;
      cancelAnimationFrame(raf);
      againBtn.disabled = true;
      player.reset();
      if (!events.length) { endOnPrint(); return; }
      // a few seconds whatever the page: long enough to watch, short enough to keep
      const duration = clamp(events.length * 70, 4000, 14000);
      const start = performance.now() + 400;   // a breath on the blank page first
      let done = 0;
      const tick = now => {
        if (closed) return;
        const want = now < start ? 0 : Math.min(events.length, Math.ceil(((now - start) / duration) * events.length));
        while (done < want) player.apply(events[done++]);
        if (done < events.length) raf = requestAnimationFrame(tick);
        else setTimeout(() => { if (!closed) endOnPrint(); }, 500);
      };
      raf = requestAnimationFrame(tick);
    }
    againBtn.addEventListener("click", play);
    play();
  }

  // ── the board ───────────────────────────────────────────────────────────
  function openBoard(page, palette, onClose) {
    const W = page.width, H = page.height;
    const session = new Date().toISOString();
    const opened = performance.now();
    document.body.style.overflow = "hidden";

    // DOM
    const wrap = h("div", "cb");
    const bar = h("div", "cb-bar");
    const backBtn = h("button", "cb-btn", "‹ Drawer");
    const title = h("div", "cb-spacer", page.title);
    const undoBtn = h("button", "cb-btn", "↶"); undoBtn.setAttribute("aria-label", "Undo");
    const redoBtn = h("button", "cb-btn", "↷"); redoBtn.setAttribute("aria-label", "Redo");
    const fitBtn = h("button", "cb-btn", "⤢"); fitBtn.setAttribute("aria-label", "Fit the page");
    const hangBtn = h("button", "cb-btn", "Hang it up");
    [backBtn, title, undoBtn, redoBtn, fitBtn, hangBtn].forEach(b => bar.appendChild(b));

    const stage = h("div", "cb-stage");
    const board = h("div", "cb-board");
    board.style.width = W + "px"; board.style.height = H + "px";
    const paint = document.createElement("canvas"); paint.width = W; paint.height = H;
    const live = document.createElement("canvas"); live.width = W; live.height = H;
    const lines = h("img"); lines.alt = ""; lines.draggable = false;
    board.appendChild(paint); board.appendChild(live); board.appendChild(lines);
    stage.appendChild(board);

    const tray = h("div", "cb-tray");
    const swatches = h("div", "cb-swatches");
    const tools = h("div", "cb-tools");
    tray.appendChild(swatches); tray.appendChild(tools);
    wrap.appendChild(bar); wrap.appendChild(stage); wrap.appendChild(tray);
    document.body.appendChild(wrap);

    const pctx = paint.getContext("2d");
    const lctx = live.getContext("2d");
    const before = document.createElement("canvas"); before.width = W; before.height = H;
    const bctx = before.getContext("2d");

    let lost = false;
    paint.addEventListener("contextlost", () => {
      lost = true; ready = false;
      saved.textContent = "hold on, getting your page back…";
    });
    paint.addEventListener("contextrestored", () => {
      const back = page.started
        ? loadImage(page.paint_url + "?t=" + Date.now())
            .then(img => { pctx.clearRect(0, 0, W, H); pctx.drawImage(img, 0, 0); })
            .catch(() => {})
        : Promise.resolve();
      back.finally(() => {
        undoStack.length = 0; redoStack.length = 0;   // their snapshots came from the lost layer
        lost = false; ready = true; saved.textContent = "";
      });
    });

    // ── crayons and tools ──
    let color = "#F4A6BC", sizeIdx = 1, mode = "brush";   // "brush" | "eraser" | "blend"
    const opacity = 0.9;               // a touch of crayon softness; layered strokes deepen
    const rows = (palette && palette.rows) || [];
    const swatchEls = [];
    for (const row of rows) {
      const fam = h("div", "cb-family");
      for (const sw of row.swatches || []) {
        const b = h("button", "cb-sw");
        b.type = "button"; b.style.background = sw.hex; b.title = sw.name + (sw.who ? " (" + sw.who + ")" : "");
        b.setAttribute("aria-label", sw.name);
        b.addEventListener("click", () => { color = sw.hex; mode = "brush"; syncTools(); });
        b.dataset.hex = sw.hex.toLowerCase();
        fam.appendChild(b); swatchEls.push(b);
      }
      swatches.appendChild(fam);
    }
    const sizeBtns = SIZES.map((px, i) => {
      const b = h("button", "cb-btn cb-size"); b.type = "button";
      const dot = h("i"); const d = Math.round(6 + i * 7); dot.style.width = d + "px"; dot.style.height = d + "px";
      b.appendChild(dot); b.setAttribute("aria-label", ["small", "medium", "big"][i] + " brush");
      b.addEventListener("click", () => { sizeIdx = i; syncTools(); });
      tools.appendChild(b); return b;
    });
    const eraseBtn = h("button", "cb-btn", "Eraser"); eraseBtn.type = "button";
    eraseBtn.addEventListener("click", () => { mode = mode === "eraser" ? "brush" : "eraser"; syncTools(); });
    tools.appendChild(eraseBtn);
    const blendBtn = h("button", "cb-btn cb-blend"); blendBtn.type = "button";
    blendBtn.appendChild(h("i")); blendBtn.appendChild(document.createTextNode("Blend"));
    blendBtn.setAttribute("aria-label", "Blending crayon");
    blendBtn.addEventListener("click", () => { mode = mode === "blend" ? "brush" : "blend"; syncTools(); });
    tools.appendChild(blendBtn);
    const saved = h("span", "cb-saved", "");
    tools.appendChild(saved);
    const now = h("span", "cb-now"); tools.appendChild(now);
    function syncTools() {
      sizeBtns.forEach((b, i) => b.classList.toggle("is-on", i === sizeIdx));
      eraseBtn.classList.toggle("is-on", mode === "eraser");
      blendBtn.classList.toggle("is-on", mode === "blend");
      swatchEls.forEach(b => b.classList.toggle("is-on", mode === "brush" && b.dataset.hex === color.toLowerCase()));
      now.style.background = mode === "eraser" ? "repeating-linear-gradient(45deg,#fff 0 4px,#eee 4px 8px)"
        : mode === "blend" ? "radial-gradient(circle,#fff 30%,#f6eef1 62%,#e4d6db 100%)" : color;
      undoBtn.disabled = !undoStack.length; redoBtn.disabled = !redoStack.length;
    }

    // ── view (pan / zoom) ──
    const view = { x: 0, y: 0, s: 1 };
    let fitScale = 1;
    function apply() { board.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.s})`; }
    function fit() {
      const r = stage.getBoundingClientRect();
      fitScale = Math.min(r.width / W, r.height / H) * 0.96;
      view.s = fitScale;
      view.x = (r.width - W * view.s) / 2;
      view.y = (r.height - H * view.s) / 2;
      apply();
    }
    function toPage(clientX, clientY) {
      const r = stage.getBoundingClientRect();
      return { x: (clientX - r.left - view.x) / view.s, y: (clientY - r.top - view.y) / view.s };
    }

    // ── undo / redo (region snapshots, so a big page doesn't eat the phone's memory) ──
    const undoStack = [], redoStack = [];
    function pushUndo(rect) {
      undoStack.push({ rect, data: bctx.getImageData(rect.x, rect.y, rect.w, rect.h) });
      if (undoStack.length > UNDO_LIMIT) undoStack.shift();
      redoStack.length = 0;
    }
    function swap(from, to, kind) {
      const step = from.pop();
      if (!step) return;
      const r = step.rect;
      to.push({ rect: r, data: pctx.getImageData(r.x, r.y, r.w, r.h) });
      pctx.putImageData(step.data, r.x, r.y);
      logEvent({ type: kind, t: Math.round(performance.now() - opened) });
      syncTools(); scheduleSave();
    }
    undoBtn.addEventListener("click", () => swap(undoStack, redoStack, "undo"));
    redoBtn.addEventListener("click", () => swap(redoStack, undoStack, "redo"));

    // ── strokes ──
    let ready = false, stroke = null, gesture = null, waitAllUp = false;
    const pointers = new Map();

    function startStroke(e) {
      const p = toPage(e.clientX, e.clientY);
      bctx.globalCompositeOperation = "copy";
      bctx.drawImage(paint, 0, 0);
      bctx.globalCompositeOperation = "source-over";
      const base = SIZES[sizeIdx] / view.s;           // page units, so it stays finger-sized on screen
      stroke = {
        id: e.pointerId, t0: performance.now(), tool: mode,
        color, base, w: base, last: p, lastT: performance.now(), pts: [],
        minX: p.x, minY: p.y, maxX: p.x, maxY: p.y, pen: e.pointerType === "pen",
        pickup: null,
      };
      if (mode === "blend") {
        blendDab(p.x, p.y, base * BLEND_REACH);
      } else {
        const erasing = mode === "eraser";
        const ctx = erasing ? pctx : lctx;
        ctx.save();
        if (erasing) ctx.globalCompositeOperation = "destination-out";
        ctx.fillStyle = erasing ? "#000" : color;
        ctx.beginPath(); ctx.arc(p.x, p.y, base / 2, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
        live.style.opacity = String(opacity);
      }
      record(p, base, 0);
    }

    // ── the blender ──
    // One dab = look at the paint under a soft round fingertip, let the carried colour drift
    // toward it (pickup), then lay the carried colour back down, strongest in the middle.
    // Pulled along a stroke, colour A walks into colour B and leaves a gradient behind it.
    // Mixing happens in PREMULTIPLIED colour, so bare paper counts as "no paint", not black:
    // dragging pink onto white paper thins it out instead of muddying it, and dragging bare
    // paper into pink softens the pink's edge. Only ever her paint layer; the lines are a
    // separate image she can't reach.
    function blendDab(cx, cy, r) {
      r = Math.max(1.5, r);
      const x0 = clamp(Math.floor(cx - r), 0, W), y0 = clamp(Math.floor(cy - r), 0, H);
      const x1 = clamp(Math.ceil(cx + r), 0, W), y1 = clamp(Math.ceil(cy + r), 0, H);
      const bw = x1 - x0, bh = y1 - y0;
      if (bw <= 0 || bh <= 0) return;
      const img = pctx.getImageData(x0, y0, bw, bh), d = img.data;
      const r2 = r * r;
      const fall = new Float32Array(bw * bh);
      let sr = 0, sg = 0, sb = 0, sa = 0, sw = 0;
      for (let j = 0; j < bh; j++) {
        const dy = y0 + j + 0.5 - cy;
        for (let i = 0; i < bw; i++) {
          const dx = x0 + i + 0.5 - cx;
          const q = 1 - (dx * dx + dy * dy) / r2;
          if (q <= 0) continue;
          const f = q * q;                               // soft, fuzzy edge
          const n = j * bw + i, k = n * 4, a = d[k + 3] / 255;
          fall[n] = f;
          sr += d[k] * a * f; sg += d[k + 1] * a * f; sb += d[k + 2] * a * f; sa += a * f; sw += f;
        }
      }
      if (!sw) return;
      const s = [sr / sw, sg / sw, sb / sw, sa / sw];
      const pk = stroke.pickup = stroke.pickup
        ? stroke.pickup.map((v, n) => v + (s[n] - v) * BLEND_PICKUP)
        : s;
      for (let n = 0; n < fall.length; n++) {
        const f = fall[n];
        if (!f) continue;
        const m = BLEND_DEPOSIT * f, k = n * 4, a = d[k + 3] / 255;
        const pa = a + (pk[3] - a) * m;
        if (pa < 0.004) { d[k + 3] = 0; continue; }
        d[k]     = (d[k] * a     + (pk[0] - d[k] * a) * m) / pa;
        d[k + 1] = (d[k + 1] * a + (pk[1] - d[k + 1] * a) * m) / pa;
        d[k + 2] = (d[k + 2] * a + (pk[2] - d[k + 2] * a) * m) / pa;
        d[k + 3] = pa * 255;
      }
      pctx.putImageData(img, x0, y0);
    }
    function blendAlong(from, to, w) {
      const r = w * BLEND_REACH;
      const step = Math.max(0.75, r * BLEND_SPACING);
      const dist = Math.hypot(to.x - from.x, to.y - from.y);
      const n = Math.max(1, Math.ceil(dist / step));
      for (let i = 1; i <= n; i++) {
        const t = i / n;
        blendDab(from.x + (to.x - from.x) * t, from.y + (to.y - from.y) * t, r);
      }
    }

    function record(p, w, dt) {
      stroke.pts.push([Math.round(p.x * 10) / 10, Math.round(p.y * 10) / 10, Math.round(dt), Math.round(w * 10) / 10]);
      stroke.minX = Math.min(stroke.minX, p.x); stroke.minY = Math.min(stroke.minY, p.y);
      stroke.maxX = Math.max(stroke.maxX, p.x); stroke.maxY = Math.max(stroke.maxY, p.y);
    }

    function addPoint(e) {
      const p = toPage(e.clientX, e.clientY);
      const t = performance.now();
      const dx = (p.x - stroke.last.x) * view.s, dy = (p.y - stroke.last.y) * view.s;
      const dist = Math.hypot(dx, dy);
      if (dist < 1.2) return;
      const speed = dist / Math.max(1, t - stroke.lastT);         // screen px per ms
      let target = stroke.base * clamp(1.18 - speed * 0.22, 0.62, 1.18);
      if (stroke.pen && e.pressure > 0 && e.pressure !== 0.5) target = stroke.base * (0.45 + e.pressure);
      stroke.w = stroke.w * 0.75 + target * 0.25;
      if (stroke.tool === "blend") {
        blendAlong(stroke.last, p, stroke.w);
        record(p, stroke.w, t - stroke.t0);
        stroke.last = p; stroke.lastT = t;
        return;
      }
      const ctx = stroke.tool === "eraser" ? pctx : lctx;
      ctx.save();
      if (stroke.tool === "eraser") ctx.globalCompositeOperation = "destination-out";
      ctx.strokeStyle = stroke.tool === "eraser" ? "#000" : stroke.color;
      ctx.lineWidth = stroke.w; ctx.lineCap = "round"; ctx.lineJoin = "round";
      ctx.beginPath(); ctx.moveTo(stroke.last.x, stroke.last.y); ctx.lineTo(p.x, p.y); ctx.stroke();
      ctx.restore();
      record(p, stroke.w, t - stroke.t0);
      stroke.last = p; stroke.lastT = t;
    }

    function strokeRect() {
      const pad = Math.ceil(stroke.base * 1.3) + 2;
      const x = clamp(Math.floor(stroke.minX - pad), 0, W), y = clamp(Math.floor(stroke.minY - pad), 0, H);
      const x2 = clamp(Math.ceil(stroke.maxX + pad), 0, W), y2 = clamp(Math.ceil(stroke.maxY + pad), 0, H);
      return { x, y, w: Math.max(1, x2 - x), h: Math.max(1, y2 - y) };
    }

    function endStroke() {
      if (!stroke) return;
      const rect = strokeRect();
      if (stroke.tool === "brush") {
        pctx.save(); pctx.globalAlpha = opacity; pctx.drawImage(live, 0, 0); pctx.restore();
        lctx.clearRect(0, 0, W, H);
      }
      pushUndo(rect);
      const ev = {
        type: "stroke", session, t: Math.round(stroke.t0 - opened), tool: stroke.tool,
        color: stroke.tool === "brush" ? stroke.color : null, opacity: stroke.tool === "brush" ? opacity : 1,
        w: Math.round(stroke.base * 10) / 10, pts: stroke.pts,
      };
      // the blender's recipe rides with its stroke, so a replay can smudge exactly the same way
      if (stroke.tool === "blend") ev.blend = { reach: BLEND_REACH, spacing: BLEND_SPACING, pickup: BLEND_PICKUP, deposit: BLEND_DEPOSIT };
      logEvent(ev);
      stroke = null;
      syncTools(); scheduleSave();
    }

    function cancelStroke() {
      if (!stroke) return;
      if (stroke.tool === "brush") lctx.clearRect(0, 0, W, H);
      else {
        const r = strokeRect();
        pctx.save(); pctx.globalCompositeOperation = "copy";
        pctx.drawImage(before, r.x, r.y, r.w, r.h, r.x, r.y, r.w, r.h);
        pctx.restore();
      }
      stroke = null;
    }

    function beginGesture() {
      const [a, b] = [...pointers.values()];
      const r = stage.getBoundingClientRect();
      gesture = {
        d0: Math.hypot(b.x - a.x, b.y - a.y) || 1,
        m0: { x: (a.x + b.x) / 2 - r.left, y: (a.y + b.y) / 2 - r.top },
        v0: { ...view },
      };
    }
    function moveGesture() {
      const [a, b] = [...pointers.values()];
      const r = stage.getBoundingClientRect();
      const d = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      const m = { x: (a.x + b.x) / 2 - r.left, y: (a.y + b.y) / 2 - r.top };
      const g = gesture;
      const s = clamp(g.v0.s * d / g.d0, fitScale * 0.5, fitScale * 10);
      const px = (g.m0.x - g.v0.x) / g.v0.s, py = (g.m0.y - g.v0.y) / g.v0.s;
      view.s = s; view.x = m.x - px * s; view.y = m.y - py * s;
      apply();
    }

    stage.addEventListener("pointerdown", e => {
      if (!ready) return;
      e.preventDefault();
      try { stage.setPointerCapture(e.pointerId); } catch (_) {}
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointers.size === 1 && !waitAllUp) {
        startStroke(e);
      } else if (pointers.size === 2) {
        if (stroke) {
          const young = performance.now() - stroke.t0 < CANCEL_IF_PINCH_WITHIN_MS || stroke.pts.length < 6;
          if (young) cancelStroke(); else endStroke();
        }
        beginGesture();
        waitAllUp = true;
      }
    });
    stage.addEventListener("pointermove", e => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (gesture && pointers.size >= 2) { moveGesture(); return; }
      if (stroke && e.pointerId === stroke.id) {
        const list = e.getCoalescedEvents ? e.getCoalescedEvents() : null;
        if (list && list.length) list.forEach(addPoint); else addPoint(e);
      }
    });
    function lift(e) {
      if (!pointers.has(e.pointerId)) return;
      pointers.delete(e.pointerId);
      if (stroke && e.pointerId === stroke.id) {
        if (e.type === "pointercancel") cancelStroke(); else endStroke();
      }
      if (pointers.size < 2) gesture = null;
      if (pointers.size === 0) waitAllUp = false;
    }
    stage.addEventListener("pointerup", lift);
    stage.addEventListener("pointercancel", lift);
    stage.addEventListener("wheel", e => {            // desktop: wheel zooms around the cursor
      e.preventDefault();
      const r = stage.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      const s = clamp(view.s * Math.exp(-e.deltaY * 0.0015), fitScale * 0.5, fitScale * 10);
      const px = (mx - view.x) / view.s, py = (my - view.y) / view.s;
      view.s = s; view.x = mx - px * s; view.y = my - py * s; apply();
    }, { passive: false });
    fitBtn.addEventListener("click", fit);

    // ── keeping her work ──
    let pending = [], saving = false, again = false, timer = null, dirty = false;
    function logEvent(ev) { pending.push(ev); dirty = true; }
    function scheduleSave() {
      clearTimeout(timer);
      saved.textContent = "…";
      timer = setTimeout(save, 900);
    }
    async function save() {
      clearTimeout(timer);
      if (!dirty && !pending.length) return true;
      if (lost || (pctx.isContextLost && pctx.isContextLost())) {
        timer = setTimeout(save, 3000);               // never send a blank, lost layer
        return false;
      }
      if (saving) { again = true; return false; }
      saving = true;
      const sending = pending.slice();
      try {
        const blob = await new Promise(res => paint.toBlob(res, "image/png"));
        const fd = new FormData();
        if (blob) fd.append("paint", blob, "paint.png");
        fd.append("events", JSON.stringify(sending));
        const r = await fetch(`${API}/page/${encodeURIComponent(page.id)}/save`, {
          method: "POST", body: fd, credentials: "same-origin",
        });
        if (!r.ok) throw new Error("HTTP " + r.status);
        pending = pending.slice(sending.length);
        dirty = pending.length > 0;
        saved.textContent = "saved ✓";
        return true;
      } catch (err) {
        saved.textContent = "saving…";
        timer = setTimeout(save, 5000);               // keep trying; her page is never dropped
        return false;
      } finally {
        saving = false;
        if (again) { again = false; scheduleSave(); }
      }
    }
    function onHidden() { if (document.visibilityState === "hidden") save(); }
    document.addEventListener("visibilitychange", onHidden);
    window.addEventListener("pagehide", save);

    // ── hang it up (the only road out of the drawer) ──
    hangBtn.addEventListener("click", () => {
      const sheet = h("div", "cb-sheet");
      sheet.appendChild(h("h3", null, "Hang it up?"));
      sheet.appendChild(h("p", null, "It stays in your drawer either way. This makes a finished copy."));
      const fridge = h("button", "cb-btn", "On the fridge (your OneDrive Pictures)");
      const frame = h("button", "cb-btn", "On the Echo picture frame");
      const no = h("button", "cb-btn", "Not yet");
      [fridge, frame, no].forEach(b => sheet.appendChild(b));
      wrap.appendChild(sheet);
      const done = msg => { sheet.innerHTML = ""; sheet.appendChild(h("h3", null, msg)); setTimeout(() => sheet.remove(), 1800); };
      no.addEventListener("click", () => sheet.remove());
      [[fridge, "fridge"], [frame, "frame"]].forEach(([b, where]) => b.addEventListener("click", async () => {
        b.disabled = true;
        await save();
        try {
          const r = await fetch(`${API}/page/${encodeURIComponent(page.id)}/hang`, {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify({ where }),
          });
          if (!r.ok) throw new Error();
          done(where === "fridge" ? "It's on the fridge. 🌸" : "It's in the picture frame. 🌸");
        } catch (_) { done("It didn't hang just now. Your page is safe; try again in a moment."); }
      }));
    });

    // ── closing ──
    backBtn.addEventListener("click", async () => {
      await save();
      document.removeEventListener("visibilitychange", onHidden);
      window.removeEventListener("pagehide", save);
      window.removeEventListener("resize", fit);
      document.body.style.overflow = "";
      wrap.remove();
      onClose && onClose();
    });
    window.addEventListener("resize", fit);

    // ── load the page ──
    syncTools();
    requestAnimationFrame(fit);
    const linesReady = loadImage(page.lines_url).then(img => { lines.src = img.src; });
    const paintReady = page.started
      ? loadImage(page.paint_url + "?t=" + Date.now()).then(img => pctx.drawImage(img, 0, 0))
      : Promise.resolve();
    Promise.all([linesReady, paintReady]).then(() => { ready = true; saved.textContent = ""; })
      .catch(() => { saved.textContent = "didn't load"; });
  }

  function boot() {
    const root = document.getElementById("coloring-room");
    if (root && !root.dataset.mounted) { root.dataset.mounted = "1"; mountDrawer(root); }
  }
  window.ColoringRoom = { mount: boot, openBoard, openTimelapse };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
