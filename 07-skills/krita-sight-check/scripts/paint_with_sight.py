#!/usr/bin/env python
"""
paint_with_sight.py: the Sight-Check reminder.

The rule, written down: NO STROKE WITHOUT SIGHT.

Run this before any Krita stroke, fill, or other modifying tool. It prints
the sight-check sequence and a fresh, absolute, timestamped export path.
You then export the canvas to that path with your Krita tools, LOOK at the
PNG, and write one sentence describing what is actually visible BEFORE
issuing any stroke.

This script runs outside any MCP context, so it cannot call Krita itself.
It is a forcing function: seeing its output is the trigger that reminds
you to look first.

USAGE
-----
    python paint_with_sight.py                    # export path under ./out/sight_checks/
    python paint_with_sight.py --out-dir DIR      # choose another folder
    (or set the SIGHT_CHECK_DIR environment variable)

After every stroke (or batch of related strokes), run it again and verify
the result before the next move.

EXIT CODES
----------
0: sequence printed and export folder ready
3: unexpected error; do NOT issue strokes
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_OUT_DIR = os.environ.get("SIGHT_CHECK_DIR", "./out/sight_checks")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print the sight-check sequence and a fresh export path.")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                    help="folder for sight-check exports (default: %(default)s)")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"sight_check_{ts}.png"

    print("=" * 60)
    print("  Sight-Check: no stroke without sight")
    print(f"  Run at: {datetime.now().isoformat(timespec='seconds')}")
    print("=" * 60)
    print()
    print("  STEP 0: Health-check Krita. If it can't connect, it's probably")
    print("          off. Open it if you're allowed; otherwise don't paint.")
    print()
    print("  STEP 1-2: Confirm the document, the ACTIVE LAYER, and the layer")
    print("            stack (document info + list layers).")
    print()
    print("  STEP 3: Export the current canvas to this exact path:")
    print(f"          {out_path}   (format=png)")
    print()
    print("  STEP 4: LOOK at the PNG with your image-viewing tool.")
    print("          Crop into anything small. Look again at ~400px.")
    print()
    print("  STEP 5: Write ONE sentence describing what is actually visible.")
    print("          Not what you think is there. Not what you intended.")
    print("          What you can SEE with your eyes open.")
    print()
    print("  STEP 6: Verify the active layer matches your intended target.")
    print("          Painting a sunrise? The active layer must NOT be the one")
    print("          holding the face. Use a dedicated layer or a new canvas.")
    print()
    print("  STEP 7: Now, and only now, issue the stroke.")
    print()
    print("  After the stroke: re-run this script. The path will be fresh.")
    print("  Look. Then stroke. Look. Then stroke.")
    print()
    print("=" * 60)
    print("  Suggested export path for this turn:")
    print(f"  {out_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        print(f"[paint_with_sight] unexpected error: {e}", file=sys.stderr)
        sys.exit(3)
