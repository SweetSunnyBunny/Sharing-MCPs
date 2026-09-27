"""Build a reviewed public starter without embedding an installation's private map.

Run with --src, --dst and --profile (a private JSON file outside both trees).
The profile requires reviewed=true and may contain replacements (regex/replacement),
forbidden regexes, exclude globs, binary_allowlist globs and an overlay_dir.
Overlays are explicitly reviewed generic replacements for personal narrative files.
Never point this tool at a live destination: it synchronizes managed source folders.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

COPY_DIRS = ['.github', 'api', 'core', 'db', 'docs', 'services', 'static', 'templates', 'tests', 'scripts', 'tools']
COPY_FILES = ['server.py', 'config.py', 'README.md', 'CODE_GUIDE.md', 'requirements.txt', 'pyproject.toml', 'pytest.ini', 'mcp-servers.json', '.env.example', '.env.paths.example', '.gitignore', 'claude-launch.sh', 'uv.lock']
TEXT_SUFFIXES = {'.py', '.md', '.json', '.html', '.js', '.css', '.txt', '.toml', '.cfg', '.ini', '.yml', '.yaml', '.sh', '.bat', '.ps1', '.cjs', '.mjs', '.svg', '.ts', '.sql', '.lock', '.example', '.kt', '.kts', '.xml', '.properties'}
EXCLUDE_DIR_NAMES = {'__pycache__', '.pytest_cache', 'node_modules', 'data', 'archive', '.git', '.mypy_cache', '.ruff_cache', '.venv', 'venv', '.wrangler'}
EXCLUDE_FILE_SUFFIXES = {'.pyc', '.pyo', '.log', '.db', '.sqlite', '.sqlite3', '.jsonl', '.pickle', '.pem', '.key'}
EXCLUDE_FILE_NAMES = {'tokens.json', 'google_tokens.json', 'client_secret.json', 'credentials.json', 'token.json'}
EXCLUDE_FILE_MARKERS = ('.bak-', '.prepatch', '.pre-', '.orig-')
EXCLUDE_FILE_ENDINGS = ('.bak', '.orig', '~')


def should_skip(path: Path) -> bool:
    return (any(part in EXCLUDE_DIR_NAMES for part in path.parts)
            or path.name in EXCLUDE_FILE_NAMES
            or any(marker in path.name.lower() for marker in EXCLUDE_FILE_MARKERS)
            or path.name.lower().endswith(EXCLUDE_FILE_ENDINGS)
            or (path.name.startswith('.env') and path.name not in {'.env.example', '.env.paths.example'})
            or path.suffix.lower() in EXCLUDE_FILE_SUFFIXES or path.is_symlink())


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in {'.gitignore', 'LICENSE', '.env.example', '.env.paths.example'} or path.name.endswith('.LICENSE')


def matches(path: Path, globs: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path.as_posix(), glob) for glob in globs)


def safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or path.drive:
        raise ValueError('Expected a relative path within the export')
    return path


def scrub_text(text: str, profile: dict) -> str:
    for rule in profile.get('replacements', []):
        text = re.sub(rule['pattern'], rule['replacement'], text, flags=rule.get('flags', 0))
    return text


def verify(root: Path, profile: dict) -> list[str]:
    problems = []
    patterns = [re.compile(value['pattern'], value.get('flags', 0)) if isinstance(value, dict) else re.compile(value) for value in profile.get('forbidden', [])]
    # Detect high-confidence credential shapes without echoing matched values.
    patterns += [re.compile(r'(?<![\w-])sk-(?:proj-|ant-|or-)?[A-Za-z0-9_-]{24,}'),
                 re.compile(r'\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}')]
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if should_skip(rel):
            problems.append(f'{rel}: excluded file')
            continue
        if not is_text(path):
            if not matches(rel, profile.get('binary_allowlist', [])):
                problems.append(f'{rel}: unreviewed binary')
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            problems.append(f'{rel}: invalid text encoding')
            continue
        if any(pattern.search(text) for pattern in patterns):
            problems.append(f'{rel}: forbidden personal/credential pattern')
    return problems


def build(src: Path, stage: Path, profile: dict) -> dict:
    records = []
    excluded = []
    paths = [path for directory in COPY_DIRS for path in (src / directory).rglob('*') if path.is_file()]
    paths += [src / name for name in COPY_FILES if (src / name).is_file()]
    # Private prompts are never read; only explicitly reviewed overlay prompts ship.
    for path in sorted(set(paths)):
        rel = path.relative_to(src)
        reason = None
        if should_skip(rel) or path.is_symlink():
            reason = 'runtime, backup, credential or symlink'
        elif matches(rel, profile.get('exclude', [])):
            reason = 'private or installation-specific material'
        elif not is_text(path) and not matches(rel, profile.get('binary_allowlist', [])):
            reason = 'unreviewed binary'
        if reason:
            excluded.append({'path': rel.as_posix(), 'reason': reason})
            continue
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        if is_text(path):
            data = scrub_text(data.decode('utf-8-sig'), profile).encode('utf-8')
        target.write_bytes(data)
        records.append({'path': rel.as_posix(), 'kind': 'source', 'sha256': hashlib.sha256(data).hexdigest()})
    overlay_root = profile.get('overlay_dir')
    if overlay_root:
        overlay_root = Path(overlay_root).resolve()
        if overlay_root == src or overlay_root.is_relative_to(src):
            raise ValueError('Reviewed overlays must be outside the private source')
        for path in sorted(overlay_root.rglob('*')):
            if not path.is_file():
                continue
            rel = path.relative_to(overlay_root)
            if should_skip(rel) or path.is_symlink():
                raise ValueError(f'Unsafe overlay path: {rel}')
            if not is_text(path) and not matches(rel, profile.get('binary_allowlist', [])):
                raise ValueError(f'Unreviewed overlay binary: {rel}')
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            records = [record for record in records if record['path'] != rel.as_posix()]
            records.append({'path': rel.as_posix(), 'kind': 'reviewed-generic-overlay', 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    problems = verify(stage, profile)
    if problems:
        raise ValueError('Public-data checks failed:\n' + '\n'.join(problems))
    # Do not expose private names in excluded filenames or source machine paths.
    return {'format': 1, 'files': sorted(records, key=lambda record: record['path']),
            'excluded_count': len(excluded), 'source_files': sum(r['kind'] == 'source' for r in records),
            'overlay_files': sum(r['kind'] != 'source' for r in records)}


def publish(stage: Path, dst: Path, dry_run: bool) -> int:
    managed = set(COPY_DIRS) | set(COPY_FILES) | {'prompts', 'scheduler', '.codex', 'wearable', 'EXPORT-MANIFEST.json', 'SHARING-NOTES.md'}
    stale = []
    for path in dst.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(dst)
        if rel.parts[0] in managed and not (stage / rel).exists():
            stale.append(path)
    if dry_run:
        return len(stale)
    dst.mkdir(parents=True, exist_ok=True)
    for path in stale:
        target = path.resolve()
        if not target.is_relative_to(dst):
            raise ValueError('Refusing deletion outside destination')
        path.unlink()
    for path in stage.rglob('*'):
        if path.is_file():
            target = dst / path.relative_to(stage)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    return len(stale)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src', type=Path, required=True)
    parser.add_argument('--dst', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    src, dst, profile_path = args.src.resolve(), args.dst.resolve(), args.profile.resolve()
    if not src.is_dir() or src == dst or src.is_relative_to(dst) or dst.is_relative_to(src):
        parser.error('Source and destination must be separate non-overlapping directories')
    if profile_path.is_relative_to(src) or profile_path.is_relative_to(dst):
        parser.error('Private profile must be outside source and destination')
    profile = json.loads(profile_path.read_text(encoding='utf-8-sig'))
    if profile.get('reviewed') is not True:
        parser.error('The private profile must explicitly set reviewed=true after review')
    if not profile.get('replacements') or not profile.get('forbidden'):
        parser.error('Provide reviewed replacements and forbidden patterns')
    with tempfile.TemporaryDirectory(prefix='anam-public-export-') as temp:
        stage = Path(temp)
        manifest = build(src, stage, profile)
        (stage / 'EXPORT-MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        stale = publish(stage, dst, args.dry_run)
        print(json.dumps({'dry_run': args.dry_run, 'files': len(manifest['files']),
                          'source_files': manifest['source_files'], 'overlay_files': manifest['overlay_files'],
                          'excluded': manifest['excluded_count'], 'stale_files': stale,
                          'privacy_check': 'passed configured checks'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
