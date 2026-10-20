const fs = require("fs");
const path = require("path");
const { chromium } = require(path.join(process.env.APPDATA, "npm/node_modules/@playwright/mcp/node_modules/playwright"));

const ANAM = "C:/Apps/anam";
const PAGE = process.argv[2];
const OUT = path.join(ANAM, "data/coloring/recovery", PAGE);
const cuts = JSON.parse(process.argv[3]);  // {"name": lastEventIndexInclusive}

const js = fs.readFileSync(path.join(ANAM, "static/js/coloring.js"), "utf8");
const grab = (start, end) => {
  const a = js.indexOf(start), b = js.indexOf(end, a);
  if (a < 0 || b < 0) throw new Error("marker missing: " + start);
  return js.slice(a, b);
};
const lib = [
  grab("const UNDO_LIMIT", "\n"),
  grab("const BLEND_REACH", "\n"),
  grab("const clamp", "\n"),
  grab("function blendDabOn", "function makeReplayer"),
  grab("function makeReplayer", "async function openTimelapse"),
].join("\n");

const events = fs.readFileSync(path.join(ANAM, "data/coloring/pages", PAGE, "strokes.jsonl"), "utf8")
  .split("\n").filter(Boolean).map(l => JSON.parse(l));
const meta = JSON.parse(fs.readFileSync(path.join(ANAM, "data/coloring/pages", PAGE, "page.json"), "utf8"));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.PW_EXE });
  const page = await browser.newPage();
  await page.setContent("<html><body></body></html>");
  await page.addScriptTag({ content: lib + "\nwindow.makeReplayer = makeReplayer;" });
  for (const [name, last] of Object.entries(cuts)) {
    const url = await page.evaluate(({ evs, W, H }) => {
      const c = document.createElement("canvas"); c.width = W; c.height = H;
      const s = document.createElement("canvas"); s.width = W; s.height = H;
      const ctx = c.getContext("2d", { willReadFrequently: true });
      const r = window.makeReplayer(ctx, s.getContext("2d"), W, H);
      r.reset();
      for (const e of evs) r.apply(e);
      return c.toDataURL("image/png");
    }, { evs: events.slice(0, last + 1), W: meta.width || 2000, H: meta.height || 3000 });
    const file = path.join(OUT, `paint_${name}.png`);
    fs.writeFileSync(file, Buffer.from(url.split(",")[1], "base64"));
    console.log("wrote", file);
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
