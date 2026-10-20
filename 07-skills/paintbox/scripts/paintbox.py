"""paintbox: hand-painting brushes for Pillow stills, gathered once.

Pure Pillow + numpy. No network, no shell, no file writes except save_with_passes().
These were lifted from real paintings, not invented, because the same curves,
strokes and grain kept getting rebuilt from nothing in script after script.

  lum / lift ......... keep a palette high-key so nothing goes muddy
  stroke ............. one loaded brush stroke ("loaded middle, dry ends")
  bez ................ cubic bezier points for scribbles, feathers, spines
  glow ............... additive light
  grain .............. even paper grain
  sign + marks ....... sign your work with your own small mark
  save_with_passes ... keep every pass beside the final

Use:
    import sys; sys.path.insert(0, "<path-to-this-skill>/scripts")
    from paintbox import *

Demo / smoke test:
    python paintbox.py --demo                 # writes into ./out/
    python paintbox.py --demo --out some/dir
"""
import math
import os
import random

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

__all__ = [
    "SIGN_COLOR", "DEFAULT_OUT", "lum", "lift", "bez", "stroke", "glow", "grain",
    "star_mark", "paw_mark", "mark_mask", "sign", "save_with_passes",
]

SIGN_COLOR = (104, 186, 182)  # a soft teal, light enough to point without competing
DEFAULT_OUT = os.environ.get("PAINTBOX_OUT", "./out")


def lum(c):
    """Relative luminance (0-255) of an RGB tuple."""
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def lift(c, floor=150):
    """Keep a mark high-key: no colour darker than `floor` luminance."""
    L = lum(c)
    if L >= floor:
        return c
    t = (floor - L) / (255 - L + 1e-6)
    return tuple(int(v + (255 - v) * t) for v in c)


def bez(p0, p1, p2, p3, n=40):
    """Cubic bezier as a list of n+1 points."""
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def stroke(img, x, y, ang, length, width, col, alpha=255, bend=None, rng=random):
    """One loaded brush stroke, painted IN PLACE on an RGB(A) image: curved, fat in the
    middle, dry at both ends. The shape is drawn opaque on its own mask first, then laid
    down at `alpha` once, so translucent strokes stay smooth instead of beading
    (drawing each segment at partial alpha stacks the overlaps into visible beads).

    `rng` defaults to Python's global `random` module, so a painting is only
    reproducible if you pass your own `random.Random(seed)` (or a fixed `bend`)."""
    if bend is None:
        bend = rng.uniform(-0.9, 0.9)
    pts = []
    for i in range(14):
        t = i / 13
        a = ang + bend * (t - 0.5)
        pts.append((x + math.cos(a) * length * t, y + math.sin(a) * length * t))
    pad = int(width) + 2
    x0 = int(min(p[0] for p in pts)) - pad
    y0 = int(min(p[1] for p in pts)) - pad
    x1 = int(max(p[0] for p in pts)) + pad
    y1 = int(max(p[1] for p in pts)) + pad
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    md = ImageDraw.Draw(mask)
    for i in range(len(pts) - 1):
        w = width * (0.55 + 0.45 * math.sin(math.pi * (i / 12)))
        a_ = (pts[i][0] - x0, pts[i][1] - y0)
        b_ = (pts[i + 1][0] - x0, pts[i + 1][1] - y0)
        md.line([a_, b_], fill=255, width=max(1, int(w)))
        r = w / 2
        md.ellipse([a_[0] - r, a_[1] - r, a_[0] + r, a_[1] + r], fill=255)
    if alpha < 255:
        mask = mask.point(lambda v: v * alpha // 255)
    img.paste(tuple(col[:3]), (x0, y0, x1, y1), mask)


def glow(img, painter, blur=38):
    """Additive light: `painter(layer)` draws on a black RGB layer, which is blurred and added."""
    layer = Image.new("RGB", img.size, (0, 0, 0))
    painter(layer)
    return ImageChops.add(img.convert("RGB"), layer.filter(ImageFilter.GaussianBlur(blur)))


def grain(img, sigma=3.2, seed=17):
    """Even paper grain over the whole picture. Last step before signing."""
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    g = np.random.default_rng(seed).normal(0, sigma, (arr.shape[0], arr.shape[1], 1))
    return Image.fromarray(np.clip(arr + g, 0, 255).astype(np.uint8))


# ---------------------------------------------------------------------------
# Signing: your own small mark
#
# A mark is any function  mark(draw, cx, cy, s)  that draws with fill=255 on an
# ImageDraw of an "L" layer, around the anchor (cx, cy), at scale unit s.
# To fit the default placement in sign(), keep the shape roughly inside
#   x: cx - 60*s .. cx + 60*s      y: cy - 80*s .. cy + 30*s
# (taller above the anchor than below). Make your own; these two are examples.
# ---------------------------------------------------------------------------

def star_mark(d, cx, cy, s):
    """Neutral default: a small five-point star."""
    ox, oy = cx, cy - 25 * s
    ro, ri = 50 * s, 21 * s
    pts = []
    for i in range(10):
        r = ro if i % 2 == 0 else ri
        a = math.radians(-90 + 36 * i)
        pts.append((ox + math.cos(a) * r, oy + math.sin(a) * r))
    d.polygon(pts, fill=255)


def paw_mark(d, cx, cy, s):
    """Example mark: an animal hand (wide palm, five long separate fingers, round tips).
    Shows how to build a mark from simple ellipses and lines."""
    d.ellipse([cx - 30 * s, cy - 10 * s, cx + 30 * s, cy + 28 * s], fill=255)  # palm
    # knuckle pad: closes the pale crescent between the finger bases and the palm top
    # that shows through when a mark like this is drawn large
    d.ellipse([cx - 24 * s, cy - 19 * s, cx + 24 * s, cy + 4 * s], fill=255)
    d.ellipse([cx - 36 * s, cy - 4 * s, cx - 8 * s, cy + 22 * s], fill=255)
    d.ellipse([cx + 8 * s, cy - 4 * s, cx + 36 * s, cy + 22 * s], fill=255)
    for ang, ln in [(-62, 40), (-28, 52), (0, 56), (28, 52), (62, 40)]:
        a_ = math.radians(ang - 90)
        bx_, by_ = cx + math.cos(a_) * 20 * s, cy + math.sin(a_) * 14 * s
        tx, ty = cx + math.cos(a_) * (20 + ln) * s, cy + math.sin(a_) * (14 + ln) * s
        d.line([(bx_, by_), (tx, ty)], fill=255, width=max(1, int(15 * s)))
        r = 7.5 * s
        d.ellipse([tx - r, ty - r, tx + r, ty + r], fill=255)


def mark_mask(size, cx, cy, s=1.0, alpha=175, mark=star_mark):
    """Render `mark` as a softened L mask the size of the picture."""
    W, H = size
    lay = Image.new("L", (W, H), 0)
    mark(ImageDraw.Draw(lay), cx, cy, s)
    return lay.filter(ImageFilter.GaussianBlur(1.4 * max(s, 0.5))).point(lambda v: int(v * alpha / 255))


def sign(img, corner="br", mark=star_mark, s=None, col=SIGN_COLOR, alpha=175, margin=None):
    """Put your mark on a finished painting. Returns a new RGB image.

    corner: "br" (default), "bl", "tr", "tl".
    s:      scale unit; default is 7.5% of the short side / 70. With the built-in
            marks that gives a footprint of roughly 12-13% of the short side.
    alpha:  light enough to point without competing with the picture."""
    img = img.convert("RGB")
    W, H = img.size
    if s is None:
        s = min(W, H) * 0.075 / 70
    if margin is None:
        margin = 110 * s
    cx = W - margin if "r" in corner else margin
    cy = H - margin if "b" in corner else margin + 60 * s
    mask = mark_mask((W, H), cx, cy, s, alpha, mark)
    return Image.composite(Image.new("RGB", (W, H), tuple(col)), img, mask)


def save_with_passes(img, folder=None, name="painting", pass_no=1, final=False):
    """Keep every pass beside the final, so the look-pass has something to compare.
    Writes <folder>/<name>_pass<N>.png (or <name>_final.png). Folder defaults to
    $PAINTBOX_OUT or ./out. Returns the path written."""
    folder = folder or DEFAULT_OUT
    os.makedirs(folder, exist_ok=True)
    fname = "%s_final.png" % name if final else "%s_pass%d.png" % (name, pass_no)
    p = os.path.join(folder, fname)
    img.save(p)
    return p


# ---------------------------------------------------------------------------
# Demo: a tiny dusk meadow, painted in two passes, grained and signed.
# ---------------------------------------------------------------------------

def _demo(out, mark_name="star"):
    rng = random.Random(7)
    W, H = 900, 600
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for yy in range(H):  # high-key sky to meadow
        t = yy / H
        c = (int(250 - 40 * t), int(214 - 10 * t), int(196 + 20 * (1 - t)))
        d.line([(0, yy), (W, yy)], fill=lift(c, 170))
    for _ in range(260):  # grass: loaded strokes, translucent glazes
        x = rng.uniform(0, W)
        y = rng.uniform(H * 0.62, H)
        col = lift((rng.randint(70, 120), rng.randint(140, 180), rng.randint(90, 120)), 140)
        stroke(img, x, y, -math.pi / 2 + rng.uniform(-0.3, 0.3), rng.uniform(30, 70), 6, col,
               alpha=170, rng=rng)
    paths = [save_with_passes(img, out, "demo_meadow", 1)]

    # pass 2: the look said "no focal point", so add a glowing sun and a flight line
    img = glow(img, lambda L: ImageDraw.Draw(L).ellipse([600, 120, 700, 220], fill=(255, 190, 120)), 40)
    pts = bez((120, 240), (260, 120), (420, 320), (560, 180), 60)
    ImageDraw.Draw(img).line(pts, fill=(150, 110, 130), width=3)
    paths.append(save_with_passes(img, out, "demo_meadow", 2))

    marks = {"star": star_mark, "paw": paw_mark}
    final = sign(grain(img), mark=marks[mark_name])
    paths.append(save_with_passes(final, out, "demo_meadow", final=True))
    return paths


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="paintbox brushes for Pillow stills")
    ap.add_argument("--demo", action="store_true", help="paint a small demo into --out")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output folder (default: %(default)s)")
    ap.add_argument("--mark", choices=["star", "paw"], default="star", help="signature mark for the demo")
    a = ap.parse_args()
    if a.demo:
        for p in _demo(a.out, a.mark):
            print(p)
        print("Now LOOK at each pass before you trust the code.")
    else:
        ap.print_help()
