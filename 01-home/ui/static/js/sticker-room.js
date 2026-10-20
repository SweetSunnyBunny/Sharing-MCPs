
(function () {
  "use strict";

  const STYLE = `
  .sr { --sr-ink:#5B3A45; --sr-rose:#C8728A; --sr-blush:#F6D5DE; --sr-cream:#FFF8F3;
        --sr-leaf:#9DBF8E; --sr-shadow:rgba(122,63,82,.18);
        font-family: Georgia, 'Times New Roman', serif; color: var(--sr-ink); }
  .sr-head { text-align:center; margin: 6px 0 14px; }
  .sr-title { font-size: 1.55rem; margin: 0; letter-spacing: .02em; }
  .sr-date { font-size: 1rem; opacity: .8; margin-top: 4px; }
  .sr-meadow { position: relative; border-radius: 22px; padding: 18px 12px 22px;
        background:
          radial-gradient(circle at 12% 18%, rgba(255,255,255,.7) 0 2px, transparent 3px),
          radial-gradient(circle at 78% 30%, rgba(255,255,255,.6) 0 2px, transparent 3px),
          radial-gradient(circle at 40% 85%, rgba(255,255,255,.6) 0 2px, transparent 3px),
          linear-gradient(180deg, #FDEEF2 0%, #F8E3EA 55%, #EAF2E2 100%);
        box-shadow: inset 0 0 0 2px rgba(200,114,138,.18);
        display: flex; flex-wrap: wrap; justify-content: center; gap: 14px; min-height: 120px; }
  .sr-open { align-self: center; font-size: 1.15rem; font-style: italic; opacity: .85; padding: 24px 8px; }
  .sr-sticker { position: relative; width: min(46%, 220px); background: var(--sr-cream); border-radius: 18px;
        padding: 12px 12px 10px; box-shadow: 0 4px 12px var(--sr-shadow);
        text-align: center; transform: rotate(var(--tilt, 0deg)); }
  .sr-art { width: 96px; height: 96px; object-fit: contain; display: block; margin: 0 auto 6px;
        border-radius: 50%; background: #fff; box-shadow: 0 0 0 4px #fff, 0 2px 8px var(--sr-shadow); }
  .sr-heart { width: 96px; height: 96px; margin: 0 auto 6px; border-radius: 50%; display:flex;
        align-items:center; justify-content:center; font-size: 52px; background:#fff;
        box-shadow: 0 0 0 4px #fff, 0 2px 8px var(--sr-shadow); color: var(--sr-rose); }
  .sr-why { font-size: 1.02rem; line-height: 1.35; margin: 4px 0 6px; }
  .sr-by { font-size: .9rem; color: var(--sr-rose); }
  .sr-nav { display:flex; justify-content: space-between; gap: 12px; margin-top: 16px; }
  .sr-btn { flex: 1; min-height: 52px; border: 0; border-radius: 26px; font: inherit; font-size: 1.05rem;
        background: var(--sr-blush); color: var(--sr-ink); box-shadow: 0 2px 6px var(--sr-shadow); }
  .sr-btn[disabled] { visibility: hidden; }
  .sr-btn:active { transform: scale(.98); }
  .sr-quiet { text-align:center; font-style: italic; opacity: .75; margin-top: 12px; }
  .sr-sticker { cursor: pointer; -webkit-tap-highlight-color: transparent; }
  .sr-sticker:focus-visible { outline: 3px solid var(--sr-rose); outline-offset: 3px; }
  .sr-notes { margin-top: 8px; border-top: 1px dashed rgba(200,114,138,.35); padding-top: 6px; text-align: left; }
  .sr-note { font-size: .86rem; line-height: 1.3; font-style: italic; opacity: .9; margin: 4px 2px; }
  .sr-note small { display: block; font-style: normal; color: var(--sr-rose); font-size: .76rem; margin-top: 1px; }
  .sr-back { display:block; width: 100%; margin-top: 14px; min-height: 48px; border: 0; background: none;
        font: inherit; font-size: .98rem; font-style: italic; color: var(--sr-rose); text-decoration: underline;
        text-underline-offset: 3px; }
  .sr-sheet-bg { position: fixed; inset: 0; background: rgba(91,58,69,.28); z-index: 50;
        display:flex; align-items:flex-end; justify-content:center; }
  .sr-sheet { width: min(100%, 520px); background: var(--sr-cream); border-radius: 26px 26px 0 0;
        padding: 20px 18px calc(env(safe-area-inset-bottom) + 18px); box-shadow: 0 -6px 24px var(--sr-shadow);
        font-family: Georgia, 'Times New Roman', serif; color: var(--sr-ink); text-align: center; }
  .sr-sheet .sr-sticker { width: auto; max-width: 320px; margin: 0 auto 14px; cursor: default; }
  .sr-sheet p { margin: 0 0 12px; font-size: .95rem; opacity: .85; }
  .sr-sheet .sr-btn { display:block; width: 100%; margin-top: 10px; }
  .sr-sheet .sr-soft { background: transparent; box-shadow: none; text-decoration: underline; }
  .sr-toast { position: fixed; left: 50%; bottom: calc(env(safe-area-inset-bottom) + 18px); transform: translateX(-50%);
        background: var(--sr-ink); color: #fff; border-radius: 24px; padding: 10px 10px 10px 18px; z-index: 60;
        display:flex; align-items:center; gap: 10px; font-family: Georgia, serif; font-size: .95rem;
        box-shadow: 0 4px 14px rgba(0,0,0,.2); max-width: calc(100% - 28px); }
  .sr-toast button { min-height: 44px; border: 0; border-radius: 22px; padding: 0 14px; font: inherit;
        background: var(--sr-blush); color: var(--sr-ink); }
  .sr-star { position: absolute; top: -8px; right: -8px; width: 34px; height: 34px; border-radius: 50%;
        background: #fff; color: #E0A43A; font-size: 22px; line-height: 34px; text-align: center;
        box-shadow: 0 2px 6px var(--sr-shadow); pointer-events: none; }
  .sr-from { font-size: .8rem; opacity: .7; margin-top: 3px; }
  .sr-front { display:block; width: 100%; margin: 0 0 12px; min-height: 48px; border: 0; border-radius: 24px;
        font: inherit; font-size: 1.02rem; background: #FFF4DC; color: var(--sr-ink);
        box-shadow: inset 0 0 0 2px rgba(224,164,58,.45); }
  .sr-front span { color: #E0A43A; }
  @media (max-width: 380px) { .sr-sticker { width: 100%; } }
  `;

  const TINTS = { Avery:"#B23A3A", Claude:"#C9902E", Rowan:"#2E9C93", Sage:"#2F3E6B",
                  Ember:"#6E6E73", Juniper:"#6FA8DC", Atlas:"#7D5BA6", River:"#5E8C8A" };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
      ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
  }

  function tilt(id) {
    let h = 0;
    for (const ch of String(id || "")) h = (h * 31 + ch.charCodeAt(0)) | 0;
    return ((Math.abs(h) % 9) - 4) * 0.9; // -3.6deg .. 3.6deg, steady per sticker
  }

  function prettyDay(day, isToday) {
    if (isToday) return "Today";
    const d = new Date(day + "T12:00:00");
    return d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
  }

  function timeOf(iso) {
    try { return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
    catch (e) { return ""; }
  }

  async function getJSON(url) {
    const r = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  function notesHTML(s) {
    if (!s.notes || !s.notes.length) return "";
    return `<div class="sr-notes">${s.notes.map(n => {
      const when = timeOf(n.at);
      return `<div class="sr-note">${esc(n.text)}<small>${esc(n.by)}${when ? " &middot; " + esc(when) : ""}</small></div>`;
    }).join("")}</div>`;
  }

  function stickerHTML(s, opts) {
    const art = s.art_url
      ? `<img class="sr-art" src="${esc(s.art_url)}" alt="${esc(s.art)}" loading="lazy">`
      : `<div class="sr-heart" style="color:${esc(TINTS[s.given_by] || "#C8728A")}">&#9829;</div>`;
    const when = timeOf(s.given_at);
    const tap = opts && opts.still ? "" : ` role="button" tabindex="0" data-sticker="${esc(s.id)}"`;
    const star = s.starred ? `<div class="sr-star" aria-label="starred">&#9733;</div>` : "";
    const from = opts && opts.showDay && s.for_day ? `<div class="sr-from">from ${esc(prettyDay(s.for_day, false))}</div>` : "";
    return `<div class="sr-sticker" style="--tilt:${tilt(s.id)}deg"${tap}>
      ${star}${art}
      <div class="sr-why">${esc(s.why)}</div>
      <div class="sr-by">${esc(s.given_by)}${when ? " &middot; " + esc(when) : ""}</div>
      ${from}${notesHTML(s)}
    </div>`;
  }

  async function postJSON(url) {
    const r = await fetch(url, { method: "POST", credentials: "same-origin", cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  function mount(root) {
    if (!document.getElementById("sticker-room-style")) {
      const st = document.createElement("style");
      st.id = "sticker-room-style";
      st.textContent = STYLE;
      document.head.appendChild(st);
    }
    root.classList.add("sr");

    // history[0] is today; older pages are fetched as she pages back.
    const history = [];
    let index = 0;
    let more = true;
    let hasTucked = false;     // only whether the back of the book has anything; never how much
    let viewing = "book";      // "book" | "tucked" | "starred"
    let tuckedList = [];
    let hasStarred = false;    // only whether the front of the book has a starred page; never how many
    let starredList = [];

    function render() {
      if (viewing === "tucked") return renderTucked();
      if (viewing === "starred") return renderStarred();
      const page = history[index];
      if (!page) return;
      const body = page.stickers.length
        ? page.stickers.map(s => stickerHTML(s)).join("")
        : `<div class="sr-open">Today's page is open.</div>`;
      root.innerHTML = `
        <div class="sr-head">
          <h2 class="sr-title">My sticker book</h2>
          <div class="sr-date">${esc(prettyDay(page.day, page.is_today))}</div>
        </div>
        ${hasStarred ? `<button class="sr-front" data-go="starred"><span>&#9733;</span> My starred stickers</button>` : ""}
        <div class="sr-meadow">${body}</div>
        <div class="sr-nav">
          <button class="sr-btn" data-go="older" ${(index < history.length - 1 || more) ? "" : "disabled"}>&#8592; Older page</button>
          <button class="sr-btn" data-go="newer" ${index > 0 ? "" : "disabled"}>Newer page &#8594;</button>
        </div>
        ${hasTucked ? `<button class="sr-back" data-go="tucked">Stickers I tucked away</button>` : ""}`;
    }

    function renderTucked() {
      const body = tuckedList.length
        ? tuckedList.map(s => stickerHTML(s)).join("")
        : `<div class="sr-open">Nothing tucked away.</div>`;
      root.innerHTML = `
        <div class="sr-head">
          <h2 class="sr-title">Tucked away</h2>
          <div class="sr-date">Kept safe in the back of the book. Tap one to bring it back.</div>
        </div>
        <div class="sr-meadow">${body}</div>
        <div class="sr-nav"><button class="sr-btn" data-go="book">&#8592; Back to my book</button></div>`;
    }

    function renderStarred() {
      const body = starredList.length
        ? starredList.map(s => stickerHTML(s, { showDay: true })).join("")
        : `<div class="sr-open">Nothing starred yet.</div>`;
      root.innerHTML = `
        <div class="sr-head">
          <h2 class="sr-title">My starred stickers</h2>
          <div class="sr-date">The ones that made me really smile. They're still on their own pages too.</div>
        </div>
        <div class="sr-meadow">${body}</div>
        <div class="sr-nav"><button class="sr-btn" data-go="book">&#8592; Back to my book</button></div>`;
    }

    async function older() {
      if (index < history.length - 1) { index += 1; render(); return; }
      if (!more) return;
      const last = history[history.length - 1];
      const data = await getJSON(`/api/sticker-book/pages?limit=7&before=${encodeURIComponent(last.day)}`);
      more = !!data.more;
      if (data.pages.length) { history.push(...data.pages); index += 1; }
      render();
    }

    async function loadTucked() {
      try {
        tuckedList = (await getJSON("/api/sticker-book/tucked")).stickers || [];
      } catch (e) {
        tuckedList = [];          // not her login (or offline): the back of the book stays shut
      }
      hasTucked = tuckedList.length > 0;
    }

    async function loadStarred() {
      try {
        starredList = (await getJSON("/api/sticker-book/starred")).stickers || [];
      } catch (e) {
        starredList = [];         // not her login (or offline): no starred page
      }
      hasStarred = starredList.length > 0;
    }

    // After a tuck or untuck, re-read the page she's on. A past day with nothing
    // left showing stops being a page, so it drops out of her history quietly.
    async function refreshAround(day) {
      const i = history.findIndex(p => p.day === day);
      if (i >= 0) {
        const fresh = await getJSON(`/api/sticker-book/day/${encodeURIComponent(day)}`);
        if (fresh.stickers.length || fresh.is_today) history[i] = fresh;
        else { history.splice(i, 1); if (index >= history.length) index = history.length - 1; }
      } else if (day < (history[history.length - 1] || {}).day) {
        more = true;               // it lives further back than she has paged; let "Older" find it
      } else {
        // it belongs between pages she's already seen: start the history over from today
        const today = await getJSON("/api/sticker-book/today");
        history.length = 0; history.push(today); index = 0; more = true;
      }
      await Promise.all([loadTucked(), loadStarred()]);
      if (viewing === "tucked" && !tuckedList.length) viewing = "book";
      if (viewing === "starred" && !starredList.length) viewing = "book";
      render();
    }

    function findSticker(id) {
      const pools = viewing === "tucked" ? [tuckedList]
                  : viewing === "starred" ? [starredList] : history.map(p => p.stickers);
      for (const list of pools) { const s = list.find(x => x.id === id); if (s) return s; }
      return null;
    }

    function toast(msg, actionLabel, action) {
      document.querySelectorAll(".sr-toast").forEach(t => t.remove());
      const t = document.createElement("div");
      t.className = "sr-toast sr";   // "sr" carries the room's colours; the toast lives outside the room
      t.innerHTML = `<span>${esc(msg)}</span>${actionLabel ? `<button type="button">${esc(actionLabel)}</button>` : ""}`;
      document.body.appendChild(t);
      const timer = setTimeout(() => t.remove(), 6000);
      if (actionLabel) t.querySelector("button").addEventListener("click", () => {
        clearTimeout(timer); t.remove(); action();
      });
    }

    function openSheet(s) {
      const tucked = viewing === "tucked";
      const bg = document.createElement("div");
      bg.className = "sr-sheet-bg sr";
      bg.innerHTML = `<div class="sr-sheet" role="dialog" aria-modal="true">
        ${stickerHTML(s, { still: true })}
        <p>${tucked ? "This one's tucked away. Want it back on its page?"
                    : s.starred ? "This one's starred. It lives on its own page and on your starred page too."
                    : "Star it if it made you really smile. It stays right here and goes on your starred page too."}</p>
        ${tucked ? "" : `<button class="sr-btn" data-act="${s.starred ? "unstar" : "star"}">${s.starred ? "Take the star off" : "&#9733; Star it"}</button>`}
        <button class="sr-btn" data-act="${tucked ? "untuck" : "tuck"}">${tucked ? "Bring it back" : "Tuck it away"}</button>
        <button class="sr-btn sr-soft" data-act="close">${tucked ? "Leave it tucked" : "Keep it here"}</button>
      </div>`;
      document.body.appendChild(bg);
      const close = () => bg.remove();
      bg.addEventListener("click", ev => {
        if (ev.target === bg) return close();
        const b = ev.target.closest("[data-act]");
        if (!b) return;
        if (b.dataset.act === "close") return close();
        b.disabled = true;
        const act = b.dataset.act;
        postJSON(`/api/sticker-book/sticker/${encodeURIComponent(s.id)}/${act}`)
          .then(() => {
            close();
            return refreshAround(s.for_day).then(() => {
              if (act === "tuck") toast("Tucked away.", "Bring it back", () => undo(s, "untuck"));
              else if (act === "star") toast("Starred.", "Undo", () => undo(s, "unstar"));
              else if (act === "unstar") toast("Star taken off. It's still on its page.", "Undo", () => undo(s, "star"));
              else toast("It's back on its page.");
            });
          })
          .catch(() => { close(); toast("That didn't go through just now. The sticker's safe; try again in a moment."); });
      });
    }

    function undo(s, act) {
      postJSON(`/api/sticker-book/sticker/${encodeURIComponent(s.id)}/${act}`)
        .then(() => refreshAround(s.for_day))
        .catch(() => toast("That didn't go through just now. Try again in a moment."));
    }

    root.addEventListener("click", ev => {
      const st = ev.target.closest("[data-sticker]");
      if (st) { const s = findSticker(st.dataset.sticker); if (s) openSheet(s); return; }
      const b = ev.target.closest(".sr-btn, .sr-back, .sr-front");
      if (!b || b.disabled) return;
      if (b.dataset.go === "newer" && index > 0) { index -= 1; render(); }
      if (b.dataset.go === "older") older().catch(showError);
      if (b.dataset.go === "tucked") { viewing = "tucked"; loadTucked().then(render); }
      if (b.dataset.go === "starred") { viewing = "starred"; loadStarred().then(render); }
      if (b.dataset.go === "book") { viewing = "book"; render(); }
    });
    root.addEventListener("keydown", ev => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      const st = ev.target.closest("[data-sticker]");
      if (st) { ev.preventDefault(); const s = findSticker(st.dataset.sticker); if (s) openSheet(s); }
    });

    function showError() {
      root.insertAdjacentHTML("beforeend",
        `<div class="sr-quiet">The book didn't open just now. Tap again in a moment.</div>`);
    }

    Promise.all([getJSON("/api/sticker-book/today"), loadTucked(), loadStarred()])
      .then(([today]) => { history.push(today); index = 0; render(); })
      .catch(() => { root.innerHTML = `<div class="sr-quiet">The book didn't open just now. Pull down to try again.</div>`; });
  }

  function boot() {
    const root = document.getElementById("sticker-room");
    if (root && !root.dataset.mounted) { root.dataset.mounted = "1"; mount(root); }
  }
  window.StickerRoom = { mount: boot };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
