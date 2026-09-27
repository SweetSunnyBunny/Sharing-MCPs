"""Build an archive from the reviewed manifest, omitting Git history and runtime data."""

from __future__ import annotations

import argparse
from pathlib import Path
import runpy
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        parser.error("Choose an output path outside this repository")
    if output.exists():
        parser.error("Output already exists; choose a new filename")

    verifier = runpy.run_path(str(ROOT / "scripts" / "verify_setups.py"))
    if verifier["main"]() != 0:
        parser.error("The snapshot changed; review and update its manifest first")

    import json
    manifest = json.loads((ROOT / "SETUP_MANIFEST.json").read_text(encoding="utf-8"))
    relative_paths = sorted(set(manifest["files"]) | {"SETUP_MANIFEST.json"})
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in relative_paths:
            archive.write(ROOT / relative, "Sharing-MCPs/" + relative)
    print(f"Created clean archive with {len(relative_paths)} reviewed files: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
