---
name: paintbox
description: A hand-painting kit for Pillow stills - lifted brushes (loaded stroke, bezier, glow, grain, high-key lift), a "sign your work with your own small mark" function, and the look-pass loop. Load whenever you start a painting by hand in Python, sign a picture you made for someone, or catch yourself rewriting a stroke function from scratch. Pairs with krita-sight-check for the seeing.
---

# paintbox

One painter counted dozens of Pillow scripts in their own folder, nearly all rebuilding the same curves, strokes and grain from nothing, with their signature living in exactly one of them. This is the box to reach into instead.

## Which tool for which picture
- **A still you paint by hand in Python** -> this box (`scripts/paintbox.py`).
- **Anything that moves** (a film, a drawing timelapse) or a named painted style (gouache, watercolour, oil) -> an animation or style-specific tool, if you have one.
- **Pixel sprites, transparent stickers, cleaning a generated PNG onto a palette** -> a pixel-art tool.
- **Painting in Krita** -> Krita MCP tools, with `krita-sight-check`.

## Before the first stroke
If the picture is of or for a person, re-read the words under the picture *right now*: their current reference, name, pronouns, the latest thing they said about themselves. Not the version in memory. People update how they want to be seen, sometimes minutes before you start. (See the `darkroom` skill for the full discipline.) Then load `krita-sight-check`; its "no stroke without sight" applies to code-paint too.

## The box
```python
import sys; sys.path.insert(0, "<path-to-this-skill>/scripts")
from paintbox import lum, lift, bez, stroke, glow, grain, sign, star_mark, paw_mark, mark_mask, save_with_passes, SIGN_COLOR
```
- `stroke(img, x, y, ang, length, width, col, alpha=255, bend=None, rng=random)`: loaded middle, dry ends, a gentle bend. Paints in place on an RGB image. Each stroke is drawn opaque on its own mask, then laid down once at `alpha`, so glazes overlap smoothly. (A first version drew each segment at partial alpha, and the overlaps beaded into visible dots.) **`rng` defaults to Python's global `random` module, so a painting is only reproducible if you pass your own `random.Random(seed)`** (or a fixed `bend`).
- `bez(p0, p1, p2, p3, n)`: cubic curve points for scribbles, feathers, spines.
- `lift(col, floor)`: keeps a palette high-key so nothing goes muddy.
- `glow(img, painter, blur)`: additive light. `painter(layer)` draws on a black layer that gets blurred and added.
- `grain(img, sigma, seed)`: paper grain, last step before signing.
- `sign(img, corner="br", mark=star_mark, s=None, col=SIGN_COLOR, alpha=175)`: puts your mark on a finished painting and returns a new image. At the default size the built-in marks stand roughly 12-13% of the short side.
- `save_with_passes(img, folder=None, name, pass_no, final=False)`: every pass saved beside the final. Folder defaults to `$PAINTBOX_OUT` or `./out/`. This is the only function in the box that writes a file.

## Sign your work with your own small mark
A signature makes a picture yours, and a small consistent one lets the person you paint for recognize your hand across everything you make. Pick a shape that means something to you (an animal print, a leaf, a star, an initial) and a colour light enough to point without competing with the picture.

A mark is any function `mark(draw, cx, cy, s)` that draws with `fill=255` on an `ImageDraw` of an `"L"` layer, around the anchor `(cx, cy)`, at scale unit `s`. To fit the default placement, keep it roughly inside `x: cx±60*s`, `y: cy-80*s .. cy+30*s`. Two examples ship in the box:
- `star_mark`: the neutral default, a five-point star.
- `paw_mark`: an animal hand built from ellipses and lines (wide palm, five long separate fingers, round tips). It shows one craft trick: a "knuckle pad" ellipse closes the pale crescent that otherwise shows between finger bases and palm when the mark is drawn large. Look at your own mark at a big scale (`s=4`) once, to find seams the default blur hides.

```python
def my_leaf(d, cx, cy, s):
    d.ellipse([cx - 22*s, cy - 70*s, cx + 22*s, cy + 10*s], fill=255)
    d.line([(cx, cy + 25*s), (cx, cy - 60*s)], fill=0, width=max(1, int(4*s)))

final = sign(grain(img), mark=my_leaf, col=(180, 140, 200))
```
Sign everything you make for someone the same way, bottom-right by default, so it becomes your hand.

## The look-pass loop (every painting, no exceptions)
1. Paint pass 1. Save it with `save_with_passes`.
2. **Look at the PNG with your own eyes.** Name one thing that's wrong in one sentence ("reads like wrapping paper", "her eyes went blue").
3. Fix only that. Save pass 2. Look again.
4. Stop when the sentence you'd write is about the feeling, not a defect. Usually 2-3 passes.
5. `grain`, then `sign`, then look one last time. Likeness first: if their eyes are green, they are green.

## Smoke test / demo
`python scripts/paintbox.py --demo` paints a small meadow in two passes, grains and signs it, and writes `demo_meadow_pass1.png`, `demo_meadow_pass2.png` and `demo_meadow_final.png` into `./out/` (`--out DIR` to change, `--mark paw` to try the other mark). Then look at each one.

## Safety
Imports `math`, `os`, `random`, `numpy` and `PIL` only. No network, no subprocess, no shell, no file reads. One write path: `save_with_passes`. Runs clean under `python -I -B`.

*Originally written for a household of AI companions; shared for anyone's companion to use.*
