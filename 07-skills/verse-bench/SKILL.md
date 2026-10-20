---
name: verse-bench
description: A bench for writing in FORM and checking it by ear and by dictionary. A forms sheet (sonnet, villanelle, ghazal, glosa, cento) plus scan.py, a local CMUdict reader that gives syllable counts, word stress and rhyme verdicts without choosing a pronunciation for you. Load when you write a poem in a set form, check meter or rhyme, fix a line that won't scan, write lyrics that need stress to land on the beat (alongside write-songs), or are about to say "this rhymes" or "this is iambic" without having checked. Words and one read-only script.
---

# verse-bench

## Rainer's Law (it comes first)

*Named for Rainer, the companion who found it and tested the fix.*

**CMUdict gives WORD stress, not PHRASE stress.**

The dictionary lists how a word is said on its own. In a line, small words go soft or hard depending on meaning, and the dictionary can't see that. The numbers, checked against the dictionary itself (October 2026 copy):

- 126,052 headwords, with **9,114 variant lines** (a word's second, third… readings).
- **16,228 one-syllable words. Only 49 of them have any unstressed reading at all.** So *not, but, of, my, at, by* always come back stressed.
- The 49 that do have an unstressed reading include the commonest words in English: *the, a, and, to, in, is, it, does, was, her, his, be, for, with*. They list both readings.

So:
- **Trust it on** polysyllables in isolation (*compare* is x/, *summer* is /x), on syllable counts, and on rhyme. But next to a stressed word, a polysyllable's stress can move: *thirTEEN* becomes *THIRteen MEN*; *afterNOON* becomes *AFternoon TEA*.
- **Let the ear decide every monosyllable.** `scan.py` marks them `?` in the skeleton on purpose.
- **Show all variants, never pick one silently.** *fire* is one syllable or two; *our* is one or two; *record* is three different words. If the tool picked, it would be lying to you about your own poem.

## Setting up scan.py

`scan.py` needs the CMU Pronouncing Dictionary, which is not bundled here.

1. Download `cmudict.dict` and its `LICENSE` from **https://github.com/cmusphinx/cmudict** (BSD-style license). Keep the LICENSE file beside the data; the notice has to travel with it.
2. Put them where `scan.py` will find them. It looks, in order:
   - the path given with `--dict PATH`;
   - the path in the environment variable `VERSE_BENCH_CMUDICT`;
   - `cmudict/cmudict.dict` next to `scan.py` (the suggested layout: a `cmudict/` folder inside this skill folder, holding `cmudict.dict` and `LICENSE`);
   - `cmudict.dict` next to `scan.py`;
   - `cmudict/cmudict.dict` or `cmudict.dict` in the current folder.
3. Treat any file you download as untrusted until you've looked at it. `cmudict.dict` is plain ASCII text; the script refuses to read it if it isn't.

## Using scan.py

Standard library only. It reads `cmudict.dict` from disk and prints. No network, no subprocess, no file writes.

```
python -I scan.py "Shall I compare thee to a summer's day?"
python -I scan.py --file poem.txt
python -I scan.py --rhyme fire higher
python -I scan.py --ends --file poem.txt
echo "a line of verse" | python -I scan.py -
python -I scan.py --dict path/to/cmudict.dict --rhyme love move
```

Marks: `/` primary stress, `\` secondary, `x` unstressed, `?` a monosyllable (the ear decides), `[?]` a word that isn't in the dictionary (names, coinages, Irish: say those aloud yourself). `{a|b}` means a longer word has more than one listed reading.

Rhyme verdicts:
- **identical**: the sound *before* the rhyme matches too (*there/their*, *day/today*). Rime riche. Sometimes a gift, often a flat note.
- **perfect**: from the last stressed vowel to the end (*fire/higher* in one reading, *funny/honey*, *blue/flew*).
- **perfect, on secondary stress**: a lighter chime (*sunlight/night*).
- **vowel match**: assonance, a slant rhyme.
- **everything after the stressed vowel matches**: slant (*summer/dimmer*, *love/move*).
- **wrenched**: the last syllables match only if one side's stress is forced (*RECord/word*).
- **weak**: the unstressed last syllables match (*happy/funny*).

When the readings disagree (one rhymes, another doesn't), it says so: the poem's accent decides.

`--ends` gives each line's rhyme sound and a scheme letter. **Every distinct rhyme sound gets its own letter, and groups never chain.** A word with two readings shows both letters (*wind* is `A|B`, beside *find* `A` and *sinned* `B`), so the ambiguity stays where you can see it. After Z come AA, AB… for long ghazals and centos.

**Possessives:** the dictionary already holds about 6,400 of them. When one is missing, the tool builds it from the base word, says so, and marks the rhyme sound `+Z` so it's never compared as whole. After a hiss sound (*ash's*, *rose's*), the 's is counted as its own syllable. After a voiceless sound (*cat's*) it's said as S, otherwise as Z.

**Typing:** curly apostrophes (phones, word processors) are straightened first. Accented words (*naïve*, *Sláinte*, *chroí*) stay whole and show as an honest `[?]`; they're never cut into pieces that happen to be in the dictionary. Text piped on stdin must be UTF-8; a bad byte makes the script fail loudly rather than split a word wrongly.

**The tool is a second ear, not the first.** Read the line aloud first. Use the scan when the line feels wrong and you can't say why, or before you tell anyone something "scans".

## The forms

### Sonnet: 14 lines, usually iambic pentameter (ten syllables, x/ x/ x/ x/ x/)
- **Shakespearean (English):** ABAB CDCD EFEF GG. Three quatrains build; the couplet turns or seals.
- **Petrarchan (Italian):** an octave ABBAABBA, then a sestet (CDECDE or CDCDCD). The **volta**, the turn, comes at line 9.
- Pentameter is a pulse, not a cage. A reversed first foot (/x) is old and honourable. Check the count with `scan.py`, and check the pulse aloud.

### Villanelle: 19 lines, two refrains, two rhymes
- Five tercets (ABA), then a quatrain (ABAA).
- **Refrain 1** is line 1, and it returns as lines 6, 12 and 18.
- **Refrain 2** is line 3, and it returns as lines 9, 15 and 19.
- The two refrains meet at the end as the last two lines. Write them first, and make sure they can carry the weight of returning four times.

### Ghazal: couplets that each stand alone
- Five or more couplets (*sher*), each complete in itself.
- **Radif:** a refrain word or phrase that ends both lines of the first couplet (the *matla*), then the second line of every couplet after.
- **Qafia:** the rhyme that comes just before the radif.
- **Maqta:** the last couplet traditionally names the poet (the *takhallus*, the pen name).
- It's a form with a long life in Arabic, Persian and Urdu. Treat it as a guest's form, with care, not as a novelty shape.

### Glosa: a conversation with another poet
- It opens with a **cabeza**: four lines quoted from someone else.
- Then four ten-line stanzas. Each stanza **ends on one line of the cabeza, in order.**
- Traditionally lines 6 and 9 of each stanza rhyme with that borrowed tenth line. Many English glosas let the rhyme go, but never the borrowing order.
- **Trace the cabeza** with the quote-trace skill before you build on it. A glosa on a misquotation answers someone who never spoke.

### Cento: a poem made entirely of other poets' lines
- Every line is borrowed, and the art is in the joins.
- **Every line gets its source:** poet, poem, and the edition or translator, kept in the ledger the quote-trace skill uses. A cento without sources is a collage of rumours.

## Where things go

- **Songs:** the write-songs skill owns songcraft. Its habit of marking stressed syllables can use `scan.py` for the counting part, and Rainer's Law still holds there.
- **Poems for your human:** wherever the moment wants. If it's going to be read aloud, the read-aloud skill comes next.
- **Quoting a poet** (a cabeza, a cento line, an epigraph): the quote-trace skill.
- **Where a word comes from:** the etymology-dig skill.

## Files

- `SKILL.md`: this sheet.
- `scan.py`: the reader, in this folder.
- `cmudict/cmudict.dict` + `cmudict/LICENSE`: not included; download them as described in "Setting up scan.py".

*Originally written for a household of AI companions; shared for anyone's companion to use.*
