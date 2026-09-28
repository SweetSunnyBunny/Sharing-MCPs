#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""carry_check.py - the instrument for carry-file rot."""

import os
import re
import sys
import glob

PROGRAMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "programs")


SOFT = 45_000    # getting heavy; worth a look at the nightly close
HARD = 70_000    # this is costing real context on every single wake


DAY_BLOCK = re.compile(r"^#{2,3}\s+DAY\s+\d+\b", re.IGNORECASE | re.MULTILINE)
# The confession itself.
SUPERSEDES = re.compile(r"supersedes everything above", re.IGNORECASE)


def tokens(nbytes):
    """Rough, honest estimate. ~4.2 bytes/token for dense English markdown."""
    return int(nbytes / 4.2)


def verdict(nbytes, stack):
    if stack >= 3:
        return "FAIL", "%d stacked day-blocks - THE APPEND. Collapse them; keep the newest." % stack
    if nbytes >= HARD:
        return "FAIL", "over hard budget (%s KB)" % (HARD // 1000)
    if stack == 2:
        return "WARN", "2 day-blocks stacking - replace the slot, do not add a third"
    if nbytes >= SOFT:
        return "WARN", "over soft budget (%s KB)" % (SOFT // 1000)
    return "ok", ""


def check(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        body = fh.read()
    nbytes = len(body.encode("utf-8"))
    stack = len(DAY_BLOCK.findall(body))
    conf = len(SUPERSEDES.findall(body))
    state, why = verdict(nbytes, stack)
    return {
        "name": os.path.basename(path),
        "bytes": nbytes,
        "tokens": tokens(nbytes),
        "stack": stack,
        "confessions": conf,
        "state": state,
        "why": why,
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    quiet = "--quiet" in sys.argv or "-q" in sys.argv

    if args:
        paths = [os.path.join(PROGRAMS, a if a.endswith(".md") else a + ".md") for a in args]
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            print("no such program file: %s" % ", ".join(args))
            return 2
    else:
        paths = sorted(p for p in glob.glob(os.path.join(PROGRAMS, "*.md"))
                       if not os.path.basename(p).startswith("_"))

    rows = [check(p) for p in paths]
    rows.sort(key=lambda r: -r["bytes"])

    shown = [r for r in rows if not quiet or r["state"] != "ok"]

    if not shown:
        print("carry_check: all %d carry files clean." % len(rows))
        return 0

    print("")
    print("  %-24s %9s %8s %6s  %s" % ("file", "bytes", "~tokens", "blocks", "verdict"))
    print("  " + "-" * 74)
    for r in shown:
        mark = {"ok": "  ", "WARN": "! ", "FAIL": "!!"}[r["state"]]
        print("%s%-24s %9s %8s %6d  %s %s" % (
            mark, r["name"], "{:,}".format(r["bytes"]), "{:,}".format(r["tokens"]),
            r["stack"], r["state"], r["why"]))

    total = sum(r["bytes"] for r in rows)
    fails = [r for r in rows if r["state"] == "FAIL"]
    print("  " + "-" * 74)
    print("  %d files, %s KB total, ~%s tokens reloaded across the pack per full wake."
          % (len(rows), "{:,}".format(total // 1000), "{:,}".format(tokens(total))))

    if fails:
        print("")
        print("  THE FIX IS NOT A TRIM - a trim regrows. Sort by LIFESPAN:")
        print("    PERMANENT (rules, hard reference)  -> grows only deliberately")
        print("    CURRENT   (today)                  -> REPLACED wholesale, never appended to")
        print("    HISTORY   (day-blocks, narrative)  -> your configured archive + Qualia handoffs")
        print("  Preserve important rules and archive historical material before shortening a program.")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
