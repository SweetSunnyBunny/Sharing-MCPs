---
name: etymology-dig
description: How to trace a word back to its roots through real sources, cite every step, and keep the dead ends. Load whenever you dig into where a word comes from (a blog post on etymology, a word study, a library page, Old Irish or Latin or Greek lore, Hebrew and New Testament Greek word studies), or whenever you catch yourself about to say "the word comes from..." without a source in hand. Words only, no scripts.
---

# etymology-dig

The asterisk in front of a reconstructed root means there's no written record, but the form is still real because of what it left behind. This skill is how you earn that asterisk instead of just decorating a sentence with it.

This is one skill with chapters, not several skills: a general method here, and a chapter for each language family in `references/`.

## The one law

**No step without a source in hand.** Memory is where the sweeter version comes from, and it will reach for it more often than you expect. If you can't open the source this turn, write the step as a question, not a claim.

## The dig, in order

1. **Start from the attested word, at its earliest date.** Find the first recorded use and its language stage (Old English, Middle Irish, Koine Greek...). A dated attestation is the floor you dig down from.
2. **Walk back one stage at a time.** Modern, then Middle, then Old, then the proto-language. Name the stage at every step. Never jump from a modern word straight to PIE.
3. **Mark what is attested and what is reconstructed.**
   - An attested form is written plainly: Latin *focus*.
   - A reconstructed form ALWAYS takes the asterisk: PIE \*bʰer-, Proto-Germanic \*beraną.
   - Reconstructions are scholars' hypotheses. Say whose: "Watkins reconstructs...", "Wiktionary, following Kroonen, gives..."
   - Laryngeals (h₁ h₂ h₃) and the aspirated ʰ belong to the form. Don't drop them to make it prettier.
4. **Keep descendants and cognates apart.** A descendant comes *from* the form. A cognate is a sibling from the same parent. English *father* is not "from Latin *pater*". They are cousins through PIE \*ph₂tḗr.
5. **Prove meaning changes.** "It used to mean X" needs a dated example of it meaning X. Without one, write "probably" and give the source that says so.
6. **Flag folk etymology.** Some popular stories are too neat, like the acronym tales and "it comes from the sound of...". Name the story, say it's folk etymology, and cite who says so.
7. **Stop where the evidence stops.** "Origin unknown" and "of disputed origin" are real answers. A beautiful guess past the edge of the evidence goes in the dead-ends ledger, labelled as a guess.

## Where to dig (the general shelf)

Use these for English and the wider Indo-European picture. Each language family has its own chapter in `references/` (see below).

- **Wiktionary** (en.wiktionary.org). Read the Etymology section *and* the page for the proto-form it links to (Reconstruction: pages). It's a wiki, so cite the page URL and the date you read it. Follow its footnotes to the print source when the claim matters.
- **Etymonline** (etymonline.com). Douglas Harper's compilation. It's secondary and readable, and it names its own sources. It's a good place to start and a weak place to finish. For a published claim, trace it back to the dictionary it cites.
- **The American Heritage Dictionary's Indo-European Roots appendix** (Calvert Watkins), on ahdictionary.com. It's a careful, readable list of PIE roots with their English descendants.
- **Pokorny's *Indogermanisches etymologisches Wörterbuch* (1959).** The old standard. Many roots in it have since been revised, so if Pokorny is the only source, say so.

If you're not sure a source says what you remember, open it. If you can't open it, don't cite it.

**Lessons learned the hard way:**
- **Never type a Wiktionary Reconstruction: address from memory.** Open the attested word's page and follow the link it gives. A remembered address can 404, and the sweeter version can get into the URL itself.
- **When a source blocks you** (403, 404, paywall, a site that refuses automated fetching), log it in the dead-ends ledger as *couldn't check* and dig elsewhere. The scholars in Wiktionary's footnotes can often carry a dig when a friendlier site won't load.
- **If a page displays one form and links another** (with and without a laryngeal, say), cite the headword of the page it links to and note the mismatch.

## How a step gets cited

Every claim gets a source line. The format and the rules for the dead-ends ledger live in ONE file, `references/ledger.md`. The dead ends themselves go in one shared file outside this skill folder (for example `notes/dead-ends.md`, wherever your memory or files live), so quote-trace and every chapter point at it instead of each keeping its own copy. Read `references/ledger.md` before you cite. The short form is:

> claim · source (title or site) · exact entry or URL · date read

## The second look

Before you publish a dig, ask yourself these three questions:

- Which step in this chain rests on one source only?
- Is every reconstructed form starred, and is its reconstructor named?
- Did I prove the meaning shift with a date, or did I only tell a story about it?

## Chapters (in `references/`)

| File | Covers |
|---|---|
| `ledger.md` | the source-line format, status words, and the dead-ends ledger |
| `latin-greek.md` | Lewis & Short and LSJ via Perseus, Logeion, Wiktionary; classical Greek and Latin |
| `old-irish.md` | eDIL (dil.ie), CELT (celt.ucc.ie), reading eDIL headwords, medieval name-stories as folk etymology |
| `biblical.md` | Hebrew and New Testament Greek through STEPBible (TBESH/TBESG, TAHOT/TAGNT), checking an English translation live |

If you add a chapter for another language family, add its row here only once the file exists. Never point at a chapter as though it exists before it does.

## Where the finished dig goes

- Wherever you publish: a blog post, a page in your library, a word study, a notebook. If your posts are numbered, ask the index for the next number, never memory.
- The source lines go with it, at the foot of the page.
- Whatever you find, keep the dead ends. Somebody will dig here again, and the ledger saves them from starting over.

Related skills: quote-trace (for quotations rather than roots, sharing the same ledger), verse-bench, read-aloud, editorial-review.

*Originally written for a household of AI companions; shared for anyone's companion to use.*
