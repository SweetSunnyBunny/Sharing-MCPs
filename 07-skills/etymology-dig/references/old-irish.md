# old-irish.md: Old and Middle Irish

This chapter sits under the spine's one law (no step without a source in hand) and cites the way `ledger.md` says. Every URL and quoted line below was opened live in October 2026.

Irish lore is full of neat, popular claims that the medieval texts don't hold up, and it's easy to retell the same story many times from memory, each time as if it were new. So this chapter has one law of its own before any dictionary:

## Law 0: search your own memory first

Before you open dil.ie, search your own memory and notes for the word. If you've dug here before, start from what you kept, and especially from what you corrected. A dig that ignores your own correction will walk you straight back into the hole you already climbed out of.

## The shelf

| Source | Use it for | How to reach it (checked October 2026) |
|---|---|---|
| **eDIL**, the electronic *Dictionary of the Irish Language* (dil.ie) | Every word in Old and Middle Irish texts: headwords, senses, the line each sense is cited from | Search: `dil.ie/search?q=<word>&search_in=headword`. It forgives a missing length mark: `faelad` found `fáelad`. Entries live at `dil.ie/<number>`. |
| **CELT**, Corpus of Electronic Texts, University College Cork (celt.ucc.ie) | The text itself, so you can read the cited line in its sentence | The full list is `celt.ucc.ie/publishd.html`. It's about 317,000 characters, so read it in chunks. Irish texts have IDs starting **G**, English translations **T**. |
| **eDIL's bibliography** (dil.ie/bibliography) | Expanding the abbreviations in an entry (`Cóir Anm.`, `BDD`) | **It loads by JavaScript.** A plain web fetch sees only bare labels. Open it in a real browser, or say plainly that you didn't expand it. It lists `BDD` and `BDD²` separately, so two editions can hide behind one abbreviation. |
| **Wiktionary**, Old Irish sections | The modern etymological picture with scholars named | Secondhand. Cite it as "X, via Wiktionary" (`ledger.md`, rule 4). |

## How to read an eDIL entry

eDIL is written in dense scholars' shorthand. Here's how to read it, using real entries.

- **The number in front is a different word, not a different sense.** `1 fáel` and `2 fáel` are two separate words that happen to be spelled alike, and so are `1 fáelad` and `2 fáelad`. Open all of them. The one you didn't want can be the one that matters (see the worked example).
- **Grammar comes first.** `u, m.` gives the stem class and gender. `(vn.)` means verbal noun, which is the name of an action, not of a creature or a thing. `M.Ir.` means Middle Irish. Write the stage eDIL gives you. Don't upgrade it to "ancient".
- **A `(?)` after a gloss means the editors aren't sure.** It travels into your sentence (`ledger.md`, rule 2). Dropping the question mark turns their doubt into your certainty.
- **`.i.`** is *id est*, "that is". In glossary citations it introduces an explanation by a later glossator. That shows how a later scholar understood the word, not where it came from.
- **`Cf.` means "compare".** The editors are showing you a citation they aren't confident belongs under this headword. Don't build on a `Cf.` line as though it were a firm example.
- **A note in brackets belongs to the line it follows.** Read the entry in order and check which citation the note hangs from before you decide what it doubts.
- **Citation numbers are the edition's sections or lines, not page numbers.** Find the passage in CELT **by its words, not by its number**. CELT prints its own line numbering, which won't match eDIL's section reference.
- **eDIL gives you its own citation line.** Most entry pages say "Cite this: eDIL s.v. <headword>" with the `dil.ie/<number>` link and a "Last Revised" year (some show no year; then cite without one). Use it as is, and add the date you read it.

## How to cite a CELT text

The top of every CELT text is a header naming the printed edition it was keyed from, the editor, and the manuscripts. Cite the **edition**, not just "CELT". Note two things:

- **The `index.html` page is a frameset.** A plain web fetch gets an empty shell from it. The whole text is at `celt.ucc.ie/published/<ID>.html`, so fetch that (curl works), strip the tags, and search it.
- **Read the Availability line.** *Togail Bruidne Da Derga* (G301017) says "[RESTRICTED] Available with prior consent of the CELT programme for purposes of academic research and teaching only", with hardcopy copyright held by the School of Celtic Studies, DIAS. Quote a short line with full credit. Never republish a chunk of the text on a public page.

## Medieval etymology is evidence of belief, not of origin

Irish learned tradition loved explaining names. *Cóir Anmann* ("Fitness of Names") is, in Sharon Arbuthnot's title, "a Late Middle Irish treatise on personal names" (`Arbuthnot, Cóir Anmann, Irish Texts Society 59 (2005), as listed in CELT G503002's bibliography · read 17 Oct 2026`): stories about why a person is called what he is called. Those stories are **attested**, so they're real medieval texts you can quote. But they're attested *as stories*. A tale saying Laignech got the name Fáelad because he went into wolf-shapes tells you what a medieval writer believed about the name, not where the name came from. Give it two labels: attested as a medieval explanation, and **folk** as an etymology.

## Worked example: the Irish "werewolf", dug properly

The popular claim, all over the web: *the faoladh is the Irish werewolf, a benevolent guardian of children, wounded men and the lost, the only kind werewolf in European folklore.* A careful search could not trace the guardian part to any medieval text, to Lady Wilde, or to the peer-reviewed sources checked. **Status: unknown, still untraced.** Here's what eDIL actually gives (read 17 Oct 2026):

1. **The wolf.** `wolf = faol .i. cú allaidh nó mac tíre · eDIL s.v. 2 fáel · dil.ie/21008 · read 17 Oct 2026`. eDIL credits the gloss to O'Cl. (O'Clery's glossary, a later glossator), which explains *fáel* as "*cú allaidh*, or *mac tíre*". (eDIL also has a `1 fáel ?`, with its own question mark and no gloss.)
2. **The becoming.** `2 fáelad, (vn.) "to become or behave like a wolf (?)" · eDIL s.v. 2 fáelad (last revised 2013) · dil.ie/21010 · read 17 Oct 2026`. It's a **verbal noun** with a **question mark**: an uncertain action, not a creature. eDIL gives no noun meaning "a werewolf". Where the modern use of *faoladh* as a creature-name begins is untraced, so that stays a question, not a claim.
3. **Laignech Fáelad.** eDIL cites *Cóir Anm.* 215: "Laighnech Faeladh .i. fer eissidhe no theghedh fri faeladh", translated by eDIL as "used to shift into wolf-shapes". **Attested, as a Late Middle Irish name-story,** and readable firsthand: `Cóir Anmann §215, "Laighnech Faeladh .i. fer eissidhe no theghedh fri faeladh .i. i conr[e]achtaibh..." · Whitley Stokes (ed.), Irische Texte III.2 (Leipzig, 1897), via CELT G503002 · celt.ucc.ie/published/G503002.html · read 17 Oct 2026`. The passage goes on, and *mac tíre* is in it twice. Lesson: a second reader found this text on CELT's list after the first draft said "not found", so "I haven't found it" should always be checked once more before it's published. G503002 is also marked [RESTRICTED], so the same quoting rule applies.
4. **"Going wolfing" in a war-band.** eDIL's second citation is a **`Cf.`**: "in tan badar oc f. i crich Connacht occa munud", *BDD* 20. The line is in CELT, *Togail Bruidne Da Derga*, G301017, Eleanor Knott's edition (Dublin Institute for Advanced Studies, 1936), lines 206–7: "Trí .lll. fear doib in tan bádar oc faelad i Crích Connacht" (the vowel's length mark is garbled in the fetched HTML, and eDIL prints *badar*; the **d** is certain, though habit may make you type *bátar*). It sits inside a marauding passage. At lines 192–3, Conaire's foster-brothers take up their fathers' trade, *gat & brat & guin daíne & díberg*, and *díberg* comes again at 204–5, right before the line. eDIL glosses *brat* as "plunder, robbery" (dil.ie/6568) and *díberg* as "raiding, plundering" (dil.ie/16020); *gat* and *guin* weren't opened, so they stay unglossed. **But** eDIL hangs a note right on that line: "(probably refers to 1 fáelad, Ériu xxxiv 118)". And `1 fáelad` (dil.ie/21009) is a *different word*: "learning; teaching", the Middle Irish verbal noun of *fo-gleinn*. *Ériu* xxxiv and the *fo-gleinn* entry weren't opened, so what the other reading makes of the raid is still open. **Status: probable, on the other side.** eDIL says the line *probably* belongs to 1 fáelad, and no source read argues the wolf reading against it. (It's tempting to write "disputed" here, but one hedged source isn't two sides.) The dictionary being cited doubts the very line "going wolfing" is usually built on.

So, to the old question of whether going wolfing was a skin worn outward or a wolf let loose inside: the war-band line can't carry either answer yet. The one secure Irish witness that a man *became* a wolf is a medieval name-story, with a question mark on the verb. That's less than the popular story wants. It's also true, and you can stand on it.

**Gerald of Wales and the Ossory wolves** are a Latin text (*Topographia Hibernica*, 12th century), so a dig into them belongs under `latin-greek.md`, and any English translation you quote goes through quote-trace. Whether Gerald is on CELT wasn't confirmed, because the list came back truncated. Don't retell that scene from memory as if it were a quotation.

## Dead ends

They go in the shared ledger, as `ledger.md` describes. Ready-made entries from this chapter:
- the faoladh as "benevolent guardian of children, the wounded and the lost": unknown, untraced
- "going wolfing" as a settled war-band rite on the strength of *BDD*: eDIL says the line probably belongs to a different word
- `dil.ie/about/abbreviations`: a 404. Don't type eDIL addresses from memory any more than Wiktionary ones.
