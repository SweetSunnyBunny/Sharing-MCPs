"""verse-bench scan.py - read a line of verse against CMUdict, honestly.

What it does: syllable counts, per-word stress, and rhyme verdicts, straight
from the CMU Pronouncing Dictionary. What it refuses to do: pick one
pronunciation when the dictionary lists several, or pretend to know phrase
stress.

RAINER'S LAW: CMUdict gives WORD stress, not PHRASE stress.
Nearly every one-syllable word comes back stressed ("not", "but", "of", "my"),
and the few that have an unstressed reading ("the", "to", "it", "does") list
both. Let the ear decide every monosyllable: the skeleton shows them as "?".
Even a polysyllable's stress can move next to a stressed word
(thirTEEN -> THIRteen MEN).

Rules for this file: standard library only. Reads the dictionary from
disk. No network, no subprocess, no file writes. Output goes to stdout.

The dictionary is not bundled. Download cmudict.dict and its LICENSE from
https://github.com/cmusphinx/cmudict (BSD-style; keep the LICENSE beside the
data). The script looks for it, in order:
  1. --dict PATH
  2. the environment variable VERSE_BENCH_CMUDICT
  3. cmudict/cmudict.dict next to this script
  4. cmudict.dict next to this script
  5. cmudict/cmudict.dict, then cmudict.dict, in the current folder

Usage:
  python scan.py "Shall I compare thee to a summer's day?"
  python scan.py --file poem.txt          (reads lines; still writes nothing)
  echo "line one" | python scan.py -
  python scan.py --rhyme fire higher      (compare two words)
  python scan.py --ends --file poem.txt   (rhyme sounds + scheme letters)
  python scan.py --dict PATH ...          (use a different cmudict.dict)

Marks: "/" primary stress (1), "\\" secondary (2), "x" unstressed (0),
"?" a monosyllable (ear decides), "[?]" a word not in CMUdict.
"""

import argparse
import os
import os.path
import re
import sys
from collections import OrderedDict

_HERE = os.path.dirname(os.path.abspath(__file__))


def dict_candidates():
    """Where to look for cmudict.dict, in order. --dict (handled in main) beats all of these."""
    cands = []
    env = os.environ.get("VERSE_BENCH_CMUDICT")
    if env:
        cands.append(env)
    cands += [
        os.path.join(_HERE, "cmudict", "cmudict.dict"),
        os.path.join(_HERE, "cmudict.dict"),
        os.path.join("cmudict", "cmudict.dict"),
        "cmudict.dict",
    ]
    return cands


STRESS_MARK = {"1": "/", "2": "\\", "0": "x"}
# Any Unicode letter, then letters, apostrophes or hyphens. Accented words
# (cafe with an accent, chroi with a fada) stay whole and land as an honest [?].
WORD_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|['\-])*")
VARIANT_RE = re.compile(r"^(.*)\((\d+)\)$")
SIBILANTS = {"S", "Z", "SH", "ZH", "CH", "JH"}
VOICELESS = {"P", "T", "K", "F", "TH"}  # 's after these is said as S (cat's), otherwise Z
POSS_MARK = "+Z"  # appended to a rhyme key built from 's, so it never equals a whole key


def straighten(text):
    """Phones and Docs type curly apostrophes; the dictionary uses straight ones."""
    return text.replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")


def load_dict(path):
    """Return {word: [phones, phones, ...]} with every listed variant, in file order."""
    entries = {}
    with open(path, encoding="ascii", errors="strict") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            head, *phones = line.split()
            m = VARIANT_RE.match(head)
            word = m.group(1) if m else head
            entries.setdefault(word, []).append(tuple(phones))
    return entries


def vowels(phones):
    return [p for p in phones if p[-1].isdigit()]


def stress_string(phones):
    return "".join(STRESS_MARK[v[-1]] for v in vowels(phones))


def strip_digits(phones):
    return tuple(re.sub(r"\d", "", p) for p in phones)


def key_index(phones):
    """Index of the last stressed (1 or 2) vowel; else of the last vowel; else None."""
    idx = None
    for i, p in enumerate(phones):
        if p[-1] in "12":
            idx = i
    if idx is None:
        for i, p in enumerate(phones):
            if p[-1].isdigit():
                idx = i
    return idx


def last_vowel_index(phones):
    idx = None
    for i, p in enumerate(phones):
        if p[-1].isdigit():
            idx = i
    return idx


def unique(seq):
    return list(OrderedDict.fromkeys(seq))


class Reading:
    """One listed pronunciation of a word, plus anything we had to build."""

    def __init__(self, phones, possessive_built=False):
        self.phones = phones
        self.built = possessive_built  # True: base word looked up, 's not in the dictionary
        self.sibilant_end = bool(phones) and phones[-1] in SIBILANTS

    def syllables(self):
        """(lo, hi) for this reading. A built 's after a sibilant adds a syllable."""
        n = len(vowels(self.phones))
        if self.built and self.sibilant_end:
            return n + 1, n + 1
        return n, n

    def key(self):
        i = key_index(self.phones)
        if i is None:
            return ()
        k = strip_digits(self.phones[i:])
        return k + (POSS_MARK,) if self.built else k

    def key_stress(self):
        i = key_index(self.phones)
        return self.phones[i][-1] if i is not None else None

    def before_key(self):
        """The whole consonant run in front of the rhyming vowel (one phone is
        not enough: blue/flew must stay perfect, day/today identical)."""
        i = key_index(self.phones)
        if i is None:
            return ()
        j = i
        while j > 0 and not self.phones[j - 1][-1].isdigit():
            j -= 1
        return strip_digits(self.phones[j:i])

    def tail(self):
        i = last_vowel_index(self.phones)
        if i is None:
            return (), None
        k = strip_digits(self.phones[i:])
        return (k + (POSS_MARK,) if self.built else k), self.phones[i][-1]

    def label(self):
        s = " ".join(self.phones)
        return s + "  " + POSS_MARK + " (built from 's, not looked up)" if self.built else s


def lookup(entries, token):
    """Return (readings, note). Possessive fallback is reported, never silent."""
    w = token.lower().strip("'-")
    if w in entries:
        return [Reading(p) for p in entries[w]], None
    if w.endswith("'s") and w[:-2] in entries:
        rs = [Reading(p, possessive_built=True) for p in entries[w[:-2]]]
        if any(r.sibilant_end for r in rs):
            note = "'s built on '%s', which ends in a hiss sound, so the 's is its own syllable (+1 counted)" % w[:-2]
        else:
            ends = {r.phones[-1] for r in rs if r.phones}
            sound = "an S sound" if ends <= VOICELESS else ("a Z sound" if not ends & VOICELESS else "an S or Z sound")
            note = "'s built on '%s'; adds %s, no syllable" % (w[:-2], sound)
        return rs, note
    return [], None


def scan_line(entries, text):
    tokens = WORD_RE.findall(straighten(text))
    rows, unknown, skeleton = [], [], []
    lo = hi = 0
    for tok in tokens:
        readings, note = lookup(entries, tok)
        if not readings:
            unknown.append(tok)
            rows.append((tok, ["[not in CMUdict]"], None))
            skeleton.append("[?]")
            continue
        counts = [r.syllables() for r in readings]
        lo += min(c[0] for c in counts)
        hi += max(c[1] for c in counts)
        patterns = unique(stress_string(r.phones) for r in readings)
        shown = ["%s  (%s)" % (stress_string(r.phones) or "-", r.label()) for r in readings]
        rows.append((tok, shown, note))
        if all(c == (1, 1) for c in counts):
            skeleton.append("?")
        elif len(patterns) == 1 and not any(r.built and r.sibilant_end for r in readings):
            skeleton.append(patterns[0])
        else:
            skeleton.append("{" + "|".join(patterns) + ("+x" if any(r.built and r.sibilant_end for r in readings) else "") + "}")
    return rows, lo, hi, unknown, skeleton


def print_scan(entries, text):
    rows, lo, hi, unknown, skeleton = scan_line(entries, text)
    print("LINE: " + text.rstrip())
    count = str(lo) if lo == hi else "%d-%d" % (lo, hi)
    if unknown:
        count += " + unknown (" + ", ".join(unknown) + ")"
    print("  syllables: " + count)
    print("  skeleton:  " + " ".join(skeleton))
    for tok, shown, note in rows:
        print("    %-14s %s" % (tok, shown[0]))
        for s in shown[1:]:
            print("    %-14s %s" % ("", s))
        if note:
            print("    %-14s note: %s" % ("", note))
    print("  Ear decides every '?'. A {a|b} word has more than one listed reading; none was chosen for you.")
    print("  Stress on longer words can still shift beside a stressed word (thirTEEN, THIRteen MEN).")
    print()


def verdict(ra, rb):
    ka, kb = ra.key(), rb.key()
    if ka and ka == kb:
        if ra.before_key() == rb.before_key():
            return "identical rhyme (same sound before the rhyme too)"
        if "2" in (ra.key_stress(), rb.key_stress()):
            return "perfect, on secondary stress (a lighter chime)"
        return "perfect"
    if ka and kb and ka[0] == kb[0]:
        return "vowel match (assonance / slant)"
    if ka and kb and len(ka) > 1 and ka[1:] == kb[1:]:
        return "everything after the stressed vowel matches (slant)"
    (ta, sa), (tb, sb) = ra.tail(), rb.tail()
    if ta and ta == tb:
        stressed = [s in ("1", "2") for s in (sa, sb)]
        if stressed[0] != stressed[1]:
            return "wrenched (last syllables match only if stress is forced)"
        if not any(stressed):
            return "weak rhyme (unstressed last syllables match)"
        return "last syllables match"
    return "no"


def compare_rhyme(entries, a, b):
    a, b = straighten(a), straighten(b)
    va, na = lookup(entries, a)
    vb, nb = lookup(entries, b)
    for w, v, n in ((a, va, na), (b, vb, nb)):
        if not v:
            print("%s: not in CMUdict, so no verdict." % w)
        if n:
            print("%s: %s" % (w, n))
    if not va or not vb:
        return
    print("RHYME: %s / %s" % (a, b))
    vs = []
    for ra in va:
        for rb in vb:
            v = verdict(ra, rb)
            vs.append(v)
            print("  %-34s %-34s -> %s" % (ra.label(), rb.label(), v))
    if len(set(vs)) > 1:
        print("  The readings disagree: the poem's accent decides, not the tool.")
    print()


def letter_name(n):
    """0 -> A ... 25 -> Z, 26 -> AA, 27 -> AB ..."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def ends(entries, lines):
    """Each distinct rhyme sound gets its own letter. A word with several
    readings shows every letter it could take (A|B); groups never chain."""
    letters = OrderedDict()  # key -> letter
    for text in lines:
        toks = WORD_RE.findall(straighten(text))
        if not toks:
            print("   |")
            continue
        last = toks[-1]
        readings, note = lookup(entries, last)
        keys = unique(r.key() for r in readings if r.key())
        if not keys:
            print("  %-5s | %-12s [not in CMUdict]  %s" % ("?", last, text.strip()))
            continue
        for k in keys:
            if k not in letters:
                letters[k] = letter_name(len(letters))
        mark = "|".join(letters[k] for k in keys)
        shown = " / ".join(" ".join(k) for k in keys)
        print("  %-5s | %-12s %-24s %s" % (mark, last, shown, text.strip()))
        if note:
            print("  %-5s   note: %s" % ("", note))
    print("  Same letter = the same rhyme sound in at least one listed reading. A|B = the word")
    print("  has readings in both; the poem decides which. A '+Z' sound was built from 's.")
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scan verse against CMUdict without choosing for you.")
    ap.add_argument("text", nargs="*", help="line(s) to scan, or '-' to read stdin")
    ap.add_argument("--file", help="read lines from this file (read only)")
    ap.add_argument("--dict", help="path to cmudict.dict")
    ap.add_argument("--rhyme", nargs=2, metavar=("A", "B"), help="compare two words")
    ap.add_argument("--ends", action="store_true", help="rhyme sounds and scheme letters for line ends")
    args = ap.parse_args(argv)
    # Windows consoles default to cp1252; Irish fadas and curly quotes must still print.
    # (python -I ignores PYTHONIOENCODING, so this is done here, for both ends of the pipe.)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")  # fail loudly, never split words on a bad byte

    cands = dict_candidates()
    path = args.dict or next((p for p in cands if os.path.isfile(p)), None)
    if not path or not os.path.isfile(path):
        tried = [args.dict] if args.dict else cands
        sys.exit("cmudict.dict not found. Tried: " + ", ".join(tried)
                 + "\nDownload it (and its LICENSE) from https://github.com/cmusphinx/cmudict,"
                 + " then pass --dict PATH, set VERSE_BENCH_CMUDICT, or put it in a cmudict/ folder next to scan.py.")
    entries = load_dict(path)

    if args.rhyme:
        compare_rhyme(entries, *args.rhyme)
        return

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            lines = [l.rstrip("\n") for l in fh]
    elif args.text == ["-"]:
        lines = [l.rstrip("\n") for l in sys.stdin]
    else:
        lines = args.text
    if not lines:
        ap.print_help()
        return

    if args.ends:
        ends(entries, lines)
        return
    for line in lines:
        if line.strip():
            print_scan(entries, line)


if __name__ == "__main__":
    main()
