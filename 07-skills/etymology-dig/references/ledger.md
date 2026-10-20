# ledger.md: the source line and the dead-ends ledger

Every other chapter, and quote-trace, points here, so there's one way to cite and one place to keep what didn't hold.

The reason this exists: it's easy to dig, find something, write it up, and lose the receipt. A page full of word roots where most entries name no source at all is a page nobody, including future-you, can check. Don't keep a finding you can't show the way back to.

## The source line

Every claim in a dig gets one line:

> `claim · source · exact entry or URL · date read`

Examples (both checked live in October 2026):

> `Latin focus = "a fire-place, hearth" · Lewis & Short (1879) via Perseus · perseus.tufts.edu/hopper/text?doc=Perseus:text:1999.04.0059:entry=focus · read 17 Oct 2026`

> `Latin focus: origin uncertain; Matasović (after Hamp) derives it from PIE *dʰegʷʰ- "to burn" · Wiktionary, Latin section · en.wiktionary.org/wiki/focus · read 17 Oct 2026`

Four rules for the line:

1. **The entry, not the site.** "etymonline" isn't a source line. The URL of the entry is.
2. **Carry the source's own hedges, word for word.** If LSJ says "prob." or "dub.", or Wiktionary says "origin uncertain" or "some connect it", that hedge goes into your sentence too. Dropping a hedge is the commonest way a guess turns into a fact on its way to a page.
3. **Say whose reconstruction it is.** An asterisked form gets its scholar's name: "Matasović (after Hamp)", "Watkins". If the source doesn't name one, write "unattributed on Wiktionary". That itself tells the reader something.
4. **Secondhand is fine if you say it's secondhand.** "de Vaan, via Wiktionary" is honest. Writing "de Vaan" alone, when you never opened de Vaan, is not. This is an easy mistake to make without noticing, so check for it on purpose.

Where the lines live: at the foot of the published page, under **Sources**, or in the draft's margin until it's published. They don't go in your memory alone. A memory can carry a source line, but the page has to carry it too.

## Status words

Use these, and only these, for how sure a step is:

| Word | Means |
|---|---|
| **attested** | written down in a dated text you can point to |
| **reconstructed** | starred, inferred by a named scholar, never written down |
| **probable** | a source says "prob." / "likely", so you say it too |
| **disputed** | sources disagree, so name both sides |
| **folk** | a popular story a source calls folk etymology |
| **unknown** | the evidence stops, so you stop too |

## The dead-ends ledger

A dead end is a claim you wanted, or once wrote, that the sources wouldn't hold up. **Keep them.** They're the most useful thing a digger leaves behind, because they mark where the ground gives way.

**The ledger itself lives outside this skill folder,** for example at `notes/dead-ends.md`, wherever your memory or files live. That's on purpose. A skill holds words that teach, and it shouldn't change every time you find a hole. Data goes on a shared shelf; the skill stays still.

An entry looks like this:

```
### <word>: <the claim I wanted>
- tempting claim: <what I or someone wrote>
- where it came from: <memory / an old dictionary / a blog / a sermon>
- what the source says: <source line>
- status: <one status word>
- found by <who>, <date>
```

Rules for the ledger:

- **Marked, not erased.** Never delete an entry. If a dead end turns out to be alive after all, add a dated line under it saying so and why. Leave the old lines standing.
- **Add before you publish.** If the dig turned up a dead end, it goes in the ledger the same turn, while you can still remember why it tempted you.
- **Read before you dig.** Search the ledger for your word first (`grep -i <word> notes/dead-ends.md`). Someone may already have fallen into the hole you're standing over.
- **Your own mistakes count most.** A dead end you walked into yourself is worth more than one you spotted in someone else's work.

## Before it goes up

The spine's second look asks three questions. The ledger adds one more:

- Does every claim on the page have a source line, and does every hedge in the sources survive into my sentence?

Then someone other than the writer should read it cold: another companion, your human, or a subagent you spawn with the draft and one instruction: check every original-language form, gloss, reconstructed root and quotation against a real source, and report what passes, what fails and what couldn't be checked, without fixing anything. The fixing stays with the writer.
