"""Verify the files listed in the setup snapshot manifest without importing apps.

Run: python scripts/verify_setups.py
This checks snapshot integrity and forbidden runtime artifacts. It does not
replace a privacy review when you add or edit files after this snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "SETUP_MANIFEST.json"
EXCLUDED_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    ".wrangler", ".claude", ".agents", "data", "logs", "cache", "dist", "build",
}
SECRET_NAMES = {".env", ".env.local", ".dev.vars", "tokens.json",
                "google_tokens.json", "client_secret.json", "credentials.json"}


def private_file(path: Path) -> bool:
    name = path.name.lower()
    example = name.endswith(".example")
    return (
        name in SECRET_NAMES
        or (not example and (name.startswith(".env") or name.startswith(".dev.vars")
                             or name.endswith(".env")))
        or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pyc", ".log",
                                  ".pem", ".key", ".pfx", ".p12", ".zip"}
        or any(marker in name for marker in (".bak", ".prepatch", ".pre-", ".orig"))
    )


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    errors: list[str] = []
    expected: set[str] = set()
    for relative, digest in manifest["files"].items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or path == ROOT:
            errors.append(f"Manifest path escapes repository: {relative}")
            continue
        canonical = path.relative_to(ROOT)
        if canonical.as_posix() != relative:
            errors.append(f"Non-canonical manifest path: {relative}")
            continue
        if any(part.lower() in EXCLUDED_DIRS for part in canonical.parts) or private_file(path):
            errors.append(f"Runtime/private manifest entry: {relative}")
            continue
        expected.add(relative)
        if not path.is_file():
            errors.append(f"Missing: {relative}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            errors.append(f"Changed: {relative}")
    for package in manifest["packages"]:
        package_root = (ROOT / package).resolve()
        if not package_root.is_relative_to(ROOT) or package_root == ROOT or not package_root.is_dir():
            errors.append(f"Invalid package path: {package}")

    # Inspect helpers and root files as well as packages. Only the repository's
    # own Git metadata is outside the reviewed distribution.
    for directory, directories, filenames in os.walk(ROOT, followlinks=False):
        parent = Path(directory)
        for name in list(directories):
            path = parent / name
            relative = path.relative_to(ROOT).as_posix()
            if parent == ROOT and name == ".git":
                directories.remove(name)
            elif path.is_symlink():
                errors.append(f"Unexpected link: {relative}")
                directories.remove(name)
            elif name.lower() in EXCLUDED_DIRS:
                errors.append(f"Runtime/private directory: {relative}")
                directories.remove(name)
        for name in filenames:
            path = parent / name
            relative = path.relative_to(ROOT).as_posix()
            if relative in {".git", "SETUP_MANIFEST.json"}:
                continue
            if path.is_symlink():
                errors.append(f"Unexpected link: {relative}")
                continue
            if private_file(path):
                errors.append(f"Runtime/private file: {relative}")
            if relative not in expected:
                errors.append(f"Unreviewed addition: {relative}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Verified {len(expected)} files across {len(manifest['packages'])} setup folders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
