# biblical.md: Hebrew and New Testament Greek

This chapter sits under the spine's one law (no step without a source in hand) and adds what's different when the word comes out of Scripture.

## The shelf

The STEPBible data is free to download from `github.com/STEPBible/STEPBible-Data`. It's made by Tyndale House, Cambridge, curated by STEPBible.org, and licensed CC BY 4.0. Keep a local copy in a folder such as `stepbible/` and treat it as read-only: don't edit those files. If you refresh them, fetch new copies and check them before replacing the old ones.

| File (names begin with) | What it gives you |
|---|---|
| `TBESH` (Translators Brief lexicon of Extended Strongs for Hebrew) | Brief Hebrew lexicon (abridged BDB), keyed by extended Strong's (`H2617a`) |
| `TBESG` (... for Greek) | Brief Greek lexicon (abridged Abbott-Smith), keyed by extended Strong's (`G5281`) |
| `TAHOT` (4 files: Gen-Deu, Jos-Est, Job-Sng, Isa-Mal) | Every word of the Hebrew OT: Hebrew, transliteration, gloss, Strong's, grammar, root |
| `TAGNT` (2 files: Mat-Jhn, Act-Rev) | Every word of the Greek NT: Greek, gloss, Strong's, grammar, dictionary form, which editions carry it |

**TBESG is biblical Greek only.** It's numbered by Strong's, so it can't reach classical words. For Plato or Homer, use `latin-greek.md` (Logeion, Perseus).

## The dig, verse first

Start from the verse, not the English word. The English is a translator's choice. The verse tells you which Hebrew or Greek word is actually on the page.

1. **Find the verse in the tagged text.** References are written `Book.Chapter.Verse#word`, e.g. `Lam.3.22#01` or `Rom.5.3#01`.
   - Hebrew: `grep -P "^Lam\.3\.22#" stepbible/TAHOT\ Isa-Mal*.txt`
   - Greek: `grep -P "^Rom\.5\.3#" stepbible/TAGNT\ Act-Rev*.txt`
   - Each line is one word: the form as written, the gloss, the Strong's number in braces (Hebrew) or `G####=grammar` (Greek), and the dictionary form.
2. **Take the Strong's number to the lexicon,** matching the case it uses: `grep -P "^H2617" stepbible/TBESH*.txt` or `grep -P "^G5281\b" stepbible/TBESG*.txt`.
3. **Read every sense the lexicon lists.** Don't stop at the one you were hoping for.
4. **Cite with the ledger line** (`ledger.md`), giving both places you looked: the verse line and the lexicon entry. For example: `hesed in Lam 3:22 · TAHOT Lam.3.22#01 {H2617A} · TBESH H2617a "goodness, kindness, faithfulness" · STEPBible CC BY 4.0 · read 17 Oct 2026`.

## Worked example: the one that's easy to get wrong

TAHOT tags the first word of Lamentations 3:22 as `H2617A`, *chesed*, kindness. TBESH has **two** entries spelled the same: `H2617a` "goodness, kindness, faithfulness" and `H2617b` "a reproach, shame". They're homonyms: two different words that happen to be spelled alike. Working from the spelling and not the tag, you could preach on the "shame" hidden inside God's mercy, and it would be a lie. **The letter after the number is the word. Always carry it through.**

## What a Bible dig needs that the spine doesn't say

- **The root fallacy.** A word means what it meant in its own sentence, not what its root meant, and not every sense in the lexicon at once. Theologians call this illegitimate totality transfer. Etymology shows how a word came to be. It doesn't settle what the word means in this verse. When a root really does shape a passage, show it from the context (the verse, the book, how the author uses it elsewhere), not just from the root. Any study that leans on a root (say, *hypomonē* and the Latin *sufferre*) should keep this line in view.
- **Shared roots are a hint, not proof.** TAHOT gives a root for many Hebrew words. That a verb and a noun share three letters tells you they're related. It doesn't tell you the noun carries the verb's picture in every verse. Say "related to" unless the context shows more.
- **The Septuagint question.** Some TBESG entries say which Hebrew word the Greek translates in the LXX (`ὑπομονή`: "in LXX for מִקְוֶה"). That's a real and useful bridge between the Testaments, but cite it as the lexicon's claim, not as something you found yourself.

## Quoting English: check the translation live

- **STEPBible's tagged English is limited.** Many popular translations (the NIV, for one) are under copyright and aren't in the STEPBible data at all. The lexicon can check your Hebrew and Greek, but it can't check the wording of a translation it doesn't hold.
- **Quote the translation the person you're writing for actually reads,** and ask if you don't know. Don't swap in a different one because it's easier to reach.
- **Every quote from a copyrighted translation gets checked live** on a site such as BibleGateway (`biblegateway.com/passage/?search=Lam+3%3A22-23&version=NIV`) in the same turn, and the check goes in the ledger line. Never quote a translation from memory. If you can't check it, put the verse in your own words and give the reference.
- **Translations and editions follow quote-trace.** An English Bible is a translation like any other: name the version, never blend two versions into a line nobody printed, and mark every cut.

## Before it goes on a public page

- **Strip the lexicon HTML** (`<b>`, `<br>`, `<ref=...>`, and TBESG's inert `javascript:void(0)` tooltip anchors). Quote plain text only. No raw lexicon markup goes on a web page.
- **Credit the data** in the study's footer: *Lexical data from STEPBible.org (Tyndale House), CC BY 4.0.*
- Then check every Hebrew and Greek word, and every quote, against its source one last time. Your human is never the proofreader.

## Dead ends

Keep them in the shared ledger with everything else (`ledger.md`). A homonym you nearly fell into, a root that turned out to be folk etymology, a sermon illustration that the lexicon wouldn't back: write them down. The next person who digs here deserves to know where the ground gives way.
