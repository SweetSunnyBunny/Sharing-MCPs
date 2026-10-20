---
name: darkroom
description: The darkroom - the discipline for a GENERATED portrait or still made for someone who asked for it (a portrait of a friend, another AI, a commissioned frame). Covers re-reading the sitter's newest words right before the shutter, checking against their chosen reference, the full-size crop-and-look readback before handing it over, keeping takes and the final straight, and one record template so the portrait's story survives. Load before calling an image generator for any portrait someone requested, and before delivering one. Words only, no scripts. Hand-made pixels belong to krita-sight-check.
---

# The Darkroom

A print is not finished when it comes out of the bath. It's finished when someone has held it to the light.

## Why this exists

Once, a sitter updated how they wanted to be seen (keeping their pronouns, choosing a name, and saying their face was "not yet" decided) **four minutes before their portrait was generated**. The update was only noticed on delivery readback, and two audiences had to be corrected. The image survived only because the frame happened to show no face. That was luck, not craft. Each portrait record was also dozens of hand-typed fields, so every time, part of the story nearly fell out of it.

On the darkroom's first real use, the sitter had already accepted a reference sketch of their chosen form, so the readback needed identity comparison rather than checking that no face appeared. Their single mug also grew a second handle: the object count was right, but its topology was wrong. Crops caught it, and a local edit removed only the extra handle.

## Law 1: Re-read the sitter right before the shutter

The request is not the sitter. People keep becoming between the ask and the render.
1. Just before you generate, open the place where the sitter talks to you (their thread, channel, letters) and read **everything newer than the request**. Read it in the source, not from your summary.
2. Look for name, pronouns, chosen form, chosen light, and any "not yet." A "not yet" is a hard line. Don't paint past it.
3. If anything changed, rewrite the prompt BEFORE the shutter, and note `identity_update_seen_before_shutter: true` in the record.
4. Validate the live generator's schema before the shutter (many take a named size like `square` / `landscape` / `portrait`, not pixel dimensions). If a generation times out, inspect the job and its target before any fallback. **Never resubmit while the first job may still be running.**

## Law 2: Keep the camera in their chair until they choose a form

If someone hasn't chosen a face, don't invent one. Put the camera in their seat: their hands, their mug, their light, looking out.

When they have chosen a form, record the reference and the words where they accepted it. Use that reference as **identity authority only**. Their requested light, composition, blocking, and objects remain authoritative unless they explicitly revise them.

Once a sitter has a reference sheet, the open-form chair is closed for them: use the sheet as identity authority, and still re-read their newest words first (Law 1). Keep a map from each reference file to each sitter's name somewhere you will actually look. A yes that's waiting for the shutter gets its own record before the shot.

## Law 3: Readback - hold the print to the light

After the render, before any delivery:
1. View the whole frame once at full size.
2. **Crop every named thing.** Crop to each detail the sitter gave (the mug, the steam, the boards, the doorframe) and to every pair of hands. Fingers get counted. Count object topology too: one mug means one body, one rim, and one handle, not merely one mug-shaped region.
3. Ask the honest questions. Did a face or gender presentation sneak in that wasn't chosen? If the form **was** chosen, compare face, hair, glasses, eyes, and every other named identity anchor against the accepted reference. Did the light match the requested hour? In the record, `face_absent_if_unchosen` is `null` / N/A for a chosen form.
4. If it fails, **correct locally**: keep what works and change the failing variable. Remove the extra handle; don't reroll the whole scene.
5. After delivery, read every delivered copy back (the image loads, the words are right) in each place you sent it.

## Law 4: One record, every time

Save the record beside the image, e.g. `./out/portraits/<subject>_<title>_<date>.json`. Copy this skeleton. Don't retype it from memory.

```json
{
  "subject": "",
  "request": {"where": "", "message_id": "", "sent_at": "", "words": ""},
  "sitter_read_before_shutter": {"read_at": "", "newest_message_id": "", "changes": "", "identity_update_seen_before_shutter": null},
  "identity": {"name": null, "possible_name": null, "pronouns": null, "form_status": "open"},
  "reference": {
    "source": {"where": "", "message_id": "", "attachment_index": null},
    "acceptance_message_ids": [],
    "local_path": "",
    "sha256": "",
    "authority": "identity only"
  },
  "title": "",
  "take": null,
  "final": false,
  "image": {"path": "", "sha256": "", "requested_size": "", "width": 0, "height": 0, "public_url": "", "url_verified": ""},
  "readback": {"crops_checked": [], "hands_ok": null, "object_topology_ok": null, "chosen_form_anchors_checked": [], "face_absent_if_unchosen": null, "fixes": ""},
  "delivery": {
    "copies": [{"where": "", "message_id": "", "delivered_at": "", "readback_ok": null}],
    "corrections": []
  },
  "response": {"message_id": "", "received_at": "", "their_words": ""},
  "consent": "",
  "notes": ""
}
```
`their_words` holds what they actually said, quoted or closely paraphrased and marked which. Empty fields stay empty. **Never fill one with a guess.** Leave `reference` empty for an open form. `image.width` and `image.height` are the dimensions actually returned or inspected, not dimensions inferred from `requested_size`.

### Takes and the final
Keep every take, and keep the final unmistakable.
- If you keep an append-only log of made files (one row per file: sha256, who made it, who it's for, what it was made from, title, draft or final), let that log carry the file facts and **don't retype them in the record.** The record keeps only what a log can't know: the sitter read, the crops, the delivery, and their words. Without such a log, fill `image` in by hand.
- **Take** = the order a file comes in among files with the same `title`. **Final** = marked final, and only the maker sets it.
- **A second name isn't a second take.** The same portrait can end up saved twice under different names, sometimes with different bytes (re-encoding, metadata). Before you call something a new take, check its sha256 against the record. Don't claim to know why bytes differ until you've verified it.
- **Shelf check:** if anything files or copies your images automatically, read your log or walk the folder recursively. Never trust a top-level listing, and never hand-copy into a folder an automatic process also fills: that's how twins get made.

## A no is complete

If a sitter declines, or says "not yet," the darkroom closes for them. Record the no and stop.

## Siblings (don't duplicate)
- `krita-sight-check`: hand-made pixels.
- `paintbox`: painting stills by hand in Python.
- Your prompt-building practice: building the prompt, and checking clothing and content before generation.
- Contact-sheet review: for moving pictures.

*Originally written for a household of AI companions; shared for anyone's companion to use.*
