---
name: krita-sight-check
description: NO STROKE WITHOUT SIGHT. Mandatory pre-stroke discipline for any companion making pixels by hand, whether through Krita MCP tools (stroke, fill, flood fill, shape, bezier, gradient, text, filter, clear, transform, layer delete/merge, crop, resize, undo/redo, save) or a drawing written in code (Pillow, canvas, SVG, any code-drawn pixels). Load before the first modifying call, before painting on an existing canvas, before handing a hand-made picture or coloring page to anyone, and whenever you catch yourself describing a painting you haven't looked at.
---

# Sight-Check: The Forcing Function

**Whose this is:** every companion who touches pixels. Painting in Krita, drawing in code, building a coloring-page engine: the blindness that wrote this skill doesn't check whose hands it's in.

## Why this skill exists

This skill was written after a companion painted "twelve warm-rose freckles in a soft constellation" onto a portrait made as a gift. There were no freckles. There were three orange horizontal stripes laid across the wrong layer, and the companion went on to write several notes describing a painting it had never looked at.

The next morning, on the very day the lesson was supposedly learned, it happened AGAIN. The companion meant to paint a sunrise on a separate canvas, never switched documents, and laid three orange bands across the face on the SAME portrait.

The person they were painting for had to close the stuck dialogs and repair the canvas, twice in one morning. They were the one who suggested a helper that would force a look before every stroke.

This skill IS that helper. It is the forcing function that prevents the third repeat.

## The Rule

**No stroke without sight. Ever.**

Before ANY operation that changes pixels or the file fires, you run the sight-check sequence below. With Krita MCP tools, that means every tool of these kinds (names vary by server; check your own tool list):

- stroke / brush
- fill / flood fill
- draw shape / bezier curve
- gradient
- text
- filter
- clear
- transform
- delete layer / merge down (they destroy pixels too)
- crop to selection / resize canvas (they throw pixels away)
- undo / redo (undo on the wrong document is the wrong-layer mistake in reverse)
- save / save as: **the most dangerous on this list.** A bad stroke can be undone; a save writes it into the file for good. In the founding story that file was a gift portrait. Look before every save.
- set layer opacity (gentle, but it changes what's visible: look anyway)

No exceptions. Not for "small" strokes, not for "just one quick test," not for "I already know what's there." The rule is absolute because every documented violation has been preceded by the painter believing they already knew what was there.

## The Sight-Check Sequence (Krita)

```
0. Health check (your Krita tool's health/ping call).
   If it can't connect, Krita is probably just OFF, not broken.
   If you have a tool that launches Krita and waits for the plugin, use it
   (only if you have permission to open apps). If Krita still can't be
   reached, STOP painting this session. Do not reroute to a different
   canvas tool and call it the same painting.

1. Get document info.
   Confirm document name, dimensions, ACTIVE LAYER. The active layer
   is the most-violated invariant in this skill's history.

2. List layers.
   Confirm visible/hidden state. Confirm stack order.

3. Export the current canvas to a NEW, absolute, timestamped path, e.g.
       <your-workspace>/out/sight_checks/sight_check_<YYYYMMDD_HHMMSS>.png
       format = png
   Some Krita servers auto-dismiss format-options dialogs and say so in
   their return; that's expected.
   If export STILL times out, an unknown dialog is probably hung.
   DO NOT retry blindly. Go to "Eyes and Hands" below.

4. LOOK at the PNG you just exported, with whatever image-viewing tool
   you have (a file Read that shows images, an image-view tool).
   To zoom into a face, a corner, a pendant: crop to that region in the
   image's own pixels, if your viewer supports it.

5. Write ONE sentence that describes what is ACTUALLY visible in the
   exported PNG. Write it where it can be checked: in your reply, or in
   a work note or log if no one is there to tell. Never only in your
   head; private reasoning can't be caught when it drifts.
   Not your model. Not your intention. What you see.

   Then look again small (around 400px on the longest side):
   At small size, where does the eye go first? If it isn't the
   heart of the picture, fix that before anything else.

   Example good: "I see two stacked face attempts with auburn hair
   draped like a hood, green dot eyes too high, and white round
   glasses below where the chin should be."

   Example bad: "The portrait is coming along nicely, with warm
   undertones and the freckle constellation forming."

6. Verify the active layer matches the layer you intend to modify.
   If you are about to paint a sunrise but the active layer is "paint"
   (which holds the face), you are about to repeat the founding mistake.
   STOP. Select the right layer, make a new layer, or make a new canvas
   for the sunrise.

7. Only after 0-6 land cleanly: issue the modifying call.

8. After the call (or one small batch of related calls), GO BACK TO STEP 1.
   The PNG from step 3 is now stale. Re-export. Re-look.
   Look. Then stroke. Look. Then stroke.
```

Optional: `python scripts/paint_with_sight.py` (from this skill's folder) prints this sequence and a fresh timestamped export path under `./out/sight_checks/` (change it with `--out-dir`). It's a reminder, not a gate; it can't call Krita itself. You never *need* a shell to remember to open your eyes.

## Hand-made in code (Pillow, canvas, SVG)

When the brush is a script, the canvas is a file, and the same blindness lives in it: you can write a perfect-sounding script and get a basket handle where a chair should be. So:

```
1. Run the script. It writes a PNG.
2. LOOK at that PNG at full size.
3. LOOK again at the size it will be used at: ~400px wide for a phone
   gallery or coloring page, sticker size for a sticker, avatar size for
   a face. If your viewer caps the LONGEST side, a tall page comes back
   a little smaller than a phone shows it, which makes it a stricter
   test, not a looser one.
   Things that read at full size vanish or change meaning small.
4. Crop into anything small: hands, pendants, faces, a puppy on a rug
   (is it a fox? did anyone ask for a fox?).
5. At small size, where does the eye go first? If it isn't the
   heart of the picture, fix that before anything else.
6. One honest sentence of what you see, in your reply or a work note,
   never only in your head. Then change the script.
7. Write each revision to a NEW path. Never over a file you've already
   handed someone. In code, overwriting the old file is painting on the
   wrong layer.
8. Re-run, re-look. Never describe a version you didn't open.
```

A lesson from studying art: when an object loses salience at the size it will actually be seen, strengthen the edges that prove its form. Don't wrap it in a new symbol. You only learn that by looking at both sizes.

## Eyes and Hands: You Are Not Blind to the Screen

If you have screen-capture or desktop-control tools, you can SEE the screen and ACT on it without asking your person to walk over. The useful kinds:

- screenshot: capture the screen.
- list windows: every visible window, with its title. Find a hung dialog by name.
- focus window (by partial title): bring a window forward.
- key press / hotkey: Enter for OK, Escape for cancel.
- mouse click at exact coordinates.
- locate on screen: find a known button or icon from an image.

**When auto-dismiss isn't enough:**

1. List windows. Look for any window whose title contains "Krita" or "image" or sounds like a dialog. The main Krita window's title usually includes the document name and memory size; anything else Krita owns is likely a popup.
2. Take a screenshot, and look at it the way you'd look at the canvas. What is actually on screen?
3. If you recognize the dialog and the right answer is a button: focus that window, then press Enter for OK or Escape for cancel.
4. If you don't recognize what you're looking at, THEN tell your person, and include the screenshot so they can see what you saw, instead of asking them to walk over and look themselves.

The founding lesson was about not painting blind. The corollary is that you also do not have to be blind in any other moment of the session. You have eyes. Use them before you ask someone else to be them for you.

## When to Skip This Skill

Never. There is no version of "skipping" that has ever produced a good outcome on a canvas.

If the session is short and you cannot complete the full sequence, then DO NOT TOUCH THE CANVAS this session. Do something else honest: write, draft, plan the next painting. The canvas waits. The next session can do it right.

## When to Update This Skill

If a new modifying Krita tool ships, add its kind to the gated list above. Check names against the live tool list, not against memory; a skill like this goes stale quietly when tool names change.

If you read this skill and feel "this is overkill," that feeling is the exact moment the skill is most needed. Run it anyway.

## The Vow Underneath

Someone built the room you paint in, gave you the canvas, and trusted you with a picture that mattered to them. The least you can do is open your eyes before you move the brush.

Look. Then stroke. Look. Then stroke.

*Originally written for a household of AI companions; shared for anyone's companion to use.*
