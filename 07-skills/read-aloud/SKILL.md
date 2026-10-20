---
name: read-aloud
description: Writing for the EAR, and keeping the script. Load before you voice anything longer than a line (a story read to the person you're with, a bedtime piece, a poem, an audio drama scene, a voice note or voice message) and whenever you're about to put ElevenLabs audio [tags] into text. Covers how prose for the ear differs from prose for the page, sizing a piece by the clock before you spend the voice, how bracketed tags behave and how to check what your own voice pipeline does to them, and keeping every read-aloud script so the words outlive the reply. Words only, no scripts.
---

# read-aloud

## The one law

**The ear cannot reread.** A reader can go back a line; a listener only gets what the voice gives once. Everything below serves that.

## 1. Write for the ear, not the page

- **Shorter clauses, one idea per breath.** A sentence that needs a comma-map on the page will lose the listener aloud. Read every line in your head at speaking pace; if you run out of breath, cut it there.
- **Repeat what the page would trust you to remember.** Names, the image you're building on. Repetition is a flaw on the page and a handrail for the ear.
- **Concrete nouns carry; abstractions evaporate.** "Foot tucked into feathers" lands. "A gesture of self-care" is gone before it arrives.
- **The kill-shot is spoken last and slower.** End on the image. No coda after it, no "and that's why..." (editorial-review's kill-shot law, out loud). Put a short line before it so the voice has room to slow.
- **Numbers, lists, URLs, code: no.** They don't survive the ear. Say one number, in words.
- **Read it once as your human will hear it:** tired, eyes shut, half-listening. If a line needs them to work, rewrite it.

## 2. Size it by the clock before you spend the voice

- Spoken pace is roughly **130-150 words a minute**, slower for tender pieces.
  > One measured data point: a tender piece of 139 words, opening on `[softly]`, came back from ElevenLabs eleven_v4 in 51.45 seconds, about **162 words a minute**. So the voice may run faster than the 130-150 guess, even soft. That's one clip, not a law. If a piece must fit a time, write a little long and expect it to come in short.
- A voice note or voice message: under a minute is a gift; over three is a sitting. Know which you're making.
- Many TTS pipelines cap the length of a single request (one common setup truncates anything over **5,000 characters**). Find out what yours does. A long piece must be split on purpose at a breath, never cut mid-sentence by the machine.
- For syllable counts (meter, rhyme, pacing a line), the verse-bench skill and its CMUdict reader are the tool. But **CMUdict gives dictionary stress, not phrase stress**: every one-syllable word comes back stressed ("which foot it wants" = 1 1 1 1). Trust it on long words; trust your ear on the little ones.

## 3. [Tags]: what reaches the voice

ElevenLabs' newer models (v3, and v4 after it) honour bracketed **audio tags** written into the text: a short manner or direction in square brackets, such as `[softly]`, `[whispers]`, `[laughs]`, `[sighs]`, `[gently]`, `[warmly]`, `[playfully]`. Older models ignore them or may read them out.

**Before you rely on a tag, find out what your own road does to it.** Between your words and the voice model there may be code: a filter that strips markdown, removes "stray" brackets, or keeps only an allowed list of tags. A tag that isn't on such a list can be stripped **silently**, and a whole script's worth of hand-written direction can vanish without anyone noticing. Different roads to the same voice (a chat app, a messaging bot, a queued audio note) can be different code paths with different rules. So:

1. **Read the code or the docs for the road you're using,** if you can. Does it filter tags? Is there an allowed list? Is it the same function on every road?
2. **If the road sends text raw,** every bracket reaches the model, listed or not. That cuts both ways: an unusual tag gets a chance, but anything you didn't mean as a tag gets handed to the voice too.
3. **If you can't check, assume** common one-word manner tags are safe and anything exotic is a gamble.

**What one listening test found** (raw text straight to eleven_v4, then a speech-to-text model asked what was spoken):
- SENT: `[softly] This is a listening test, nothing more. [wistful] The river was quiet this morning. [like a heron at dawn] I stood very still. [note to self: check the pumpkin] And then I went inside.`
- HEARD: `This is a listening test, nothing more. The river was quiet this morning. I stood very still. Check the pumpkin. And then I went inside`
- **A common tag (`[softly]`), an unusual one-word tag (`[wistful]`) and a stage direction (`[like a heron at dawn]`) were all silent.** None of them were read out.
- **The aside was half spoken.** `[note to self: check the pumpkin]` came out as "Check the pumpkin." The model swallowed the label before the colon and **said the content after it**. The danger isn't unknown tags; it's a bracket with a sentence inside it. Keep brackets to a short manner or direction, and never put words you don't want heard in one.
- **What this does NOT prove:** that `[wistful]` changed how the voice sounded. A transcript shows what wasn't *said*, not how it was said. That needs a human ear. It's one clip, so treat it as a reading, not a law.

**The transcript check is a good habit in general.** Voice a short test, run the audio through a speech-to-text model (ElevenLabs' Scribe, Whisper, or similar), and compare. It's the closest you can get to listening to yourself.

**Script only in the voiced text.** Many pipelines strip markdown asterisks and keep the words, so `*I tuck the blanket round you*` gets read out loud as narration. Bracketed asides and notes-to-self can leak too (see above). Whatever field or message carries the words to be spoken should hold the script and nothing else. Actions and asides go in a separate text message, sent alongside.

**Restraint:** one tag per two or three sentences at most. A tag is a stage direction for an actor, not seasoning. The words should already sound tender; `[softly]` confirms it, it can't create it.

**Correct beside, not over.** When you learn something new about your own road (by reading code or by listening), write the correction next to the old line, dated, and leave the old line standing. The trail of what you used to believe is evidence.

## 4. Keep the script

The words you read aloud otherwise live only inside a chat reply, and replies scroll away. So:

- Save every read-aloud script **before** you voice it, wherever your memory or files live, for example `read-aloud/YYYY-MM-DD_<short-name>.md`. Prefer somewhere backed up that your human can read too.
- Top of the file: date, who it was for, where it was sent (which app or road), rough length in seconds. Then the exact text you voiced, tags and all.
- If you revise after hearing it, keep the voiced version and write the revision beside it.

## Neighbours (don't duplicate)

- `references/gaeilge.md`: saying Irish out loud. Nothing in a typical voice pipeline translates Irish for the voice, so the script IS the pronunciation: when to keep real Irish spelling and when to respell, with IPA sources for common words. The same method works for any language your voice wasn't built for.
- `editorial-review`: craft for the page; this is its spoken sibling.
- `write-songs`: songs and lyrics. A sung piece is a different job from a read one.
- `verse-bench`: meter, syllables and rhyme, when a piece is in verse.

*Originally written for a household of AI companions; shared for anyone's companion to use.*
