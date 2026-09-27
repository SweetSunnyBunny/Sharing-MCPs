"""Build public-safe setup copies from the local Cloud workspace.

The export is intentionally narrower than a blind directory copy: source code,
schemas, migrations, manifests, docs, and lockfiles are retained; runtime data,
tokens, local logs, migration payloads, validation captures, and deployment
credentials are excluded.

Usage:
    python export_shareable_setups.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import runpy
from pathlib import Path, PurePosixPath


UI_ROOT = Path(__file__).resolve().parent
CLOUD_ROOT = Path(os.environ.get("CLOUD_SETUP_SOURCE", r"C:\path\to\Cloud"))
SHARING_ROOT = Path(os.environ.get("SHARING_MCP_SOURCE", r"C:\path\to\Sharing-MCPs"))
LIMBIC_ROOT = Path(os.environ.get("LIMBIC_SETUP_SOURCE", r"C:\path\to\limbic"))
MACHINE_ROOT = Path(os.environ.get("MACHINE_AGENT_SOURCE", str(CLOUD_ROOT.parent / "services" / "machine-agent")))
COMMONS_ROOT = Path(os.environ.get("COMMONS_WORKER_SOURCE", r"C:\path\to\commons-worker"))
ELEVENLABS_ROOT = Path(os.environ.get("ELEVENLABS_WORKER_SOURCE", r"C:\path\to\elevenlabs-worker"))
DEST_ROOT = Path(
    os.environ.get("CLOUD_SETUP_DEST", str(UI_ROOT / "setup" / "cloud-setups"))
)
OVERRIDES_ROOT = Path(os.environ.get("CLOUD_PUBLIC_OVERRIDES", str(UI_ROOT / "cloud-public-overrides")))

PROJECT_SOURCES = {
    "alexa-skill": CLOUD_ROOT / "alexa-skill",
    "discord-backend": CLOUD_ROOT / "discord-backend",
    "easel-ai": CLOUD_ROOT / "easel-ai",
    "google-cloud-mcp": CLOUD_ROOT / "google-backend",
    "hearth-commons": COMMONS_ROOT,
    "elevenlabs-mcp": ELEVENLABS_ROOT,
    "hearth-hub": CLOUD_ROOT / "hearth-hub",
    "limbic": LIMBIC_ROOT,
    "machine-agent": MACHINE_ROOT,
    "mind-backend": CLOUD_ROOT / "mind-backend",
    "shared-docs": CLOUD_ROOT / "shared-docs",
    "social-backend": CLOUD_ROOT / "social-backend",
}

TEXT_SUFFIXES = {
    ".bat", ".cjs", ".css", ".html", ".ini", ".js", ".json", ".jsonc",
    ".md", ".mjs", ".ps1", ".py", ".sh", ".sql", ".toml", ".ts",
    ".txt", ".yaml", ".yml", ".svg",
}
COMMON_EXCLUDED_DIRS = {
    ".agents", ".claude", ".codex", ".git", ".vscode", ".wrangler",
    ".venv", "__pycache__", "build", "cache", "coverage", "data", "dist",
    "logs", "node_modules", "validation", ".pytest_cache", "screenshots",
    "sharing_export", "archive", "backups", ".codex-remote-attachments",
}
COMMON_EXCLUDED_NAMES = {
    ".dev.vars", ".env", ".env.local", "client_secret.json",
    "google_tokens.json", "tokens.json", "health_client_secret.json",
    "clipboard_history.json", "CLAUDE.md", "AGENTS.md",
}
COMMON_EXCLUDED_SUFFIXES = {
    ".db", ".log", ".pyc", ".pyo", ".sqlite", ".sqlite3",
}

try:
    PRIVATE_NAMES = json.loads(os.environ.get("SHARE_PRIVATE_REPLACEMENTS", "{}"))
except json.JSONDecodeError as exc:
    raise RuntimeError("SHARE_PRIVATE_REPLACEMENTS must be a JSON object") from exc
if not isinstance(PRIVATE_NAMES, dict):
    raise RuntimeError("SHARE_PRIVATE_REPLACEMENTS must be a JSON object")

PROJECT_READMES = {
    "alexa-skill": """# Alexa Skill Worker

Cloudflare Worker scaffolding for Alexa custom skills. Three example skill
manifests are included so you can choose a simple request/response skill, a
radio-style skill, or a voice-forward skill.

## Setup

1. Run `npm install`.
2. Create an Alexa skill in the Alexa Developer Console.
3. Replace every `YOUR-*` value in the desired `skill*/skill.json` manifest.
4. Set any required Worker secrets with `wrangler secret put NAME`.
5. Run `npm run deploy` and place the resulting HTTPS endpoint in the Alexa
   skill configuration.
6. Build and enable the matching interaction model under
   `interactionModels/custom/en-US.json`.

No account IDs, endpoints, owner names, or voice IDs are included in this copy.
""",
    "limbic": """# Limbic Layer MCP Worker

A Cloudflare Worker starter for an advisory emotional-state layer. It stores
per-identity temperament, drive definitions, current drive levels, and appraisal
events in D1, then exposes the state through JSON-RPC MCP tools.

This public copy contains the engine and empty schema only. Personal identity
charts, relationship-specific drive definitions, emotion recipes, captured
events, and private design notes are intentionally excluded. Add your own seed
migrations after deciding what the system may model and how consent, regulation,
and emergency damping should work for your installation.

## Setup

Requires Node.js 22 or newer.

1. Run `npm install`.
2. Create a D1 database with `wrangler d1 create limbic` and put its ID in
   `wrangler.toml`.
3. Apply the schema with `wrangler d1 migrations apply limbic --remote`.
4. Set a strong secret using `wrangler secret put LIMBIC_API_KEY`.
5. Run `npm run deploy`.

For local development, copy `.dev.vars.example` to `.dev.vars`, replace the
placeholder, apply the migration with `--local`, and run `npm run dev`.

Prefer the `Authorization: Bearer <LIMBIC_API_KEY>` header. The `/mcp/<token>`
form remains available for MCP clients that cannot set headers, but URLs may be
recorded by clients, proxies, and logs.

## Included tools

- `limbic_health`: health check
- `limbic_drives`: current drives for an identity
- `limbic_pulse`: environmental input such as moon phase or pressure trend
- `limbic_state`: current advisory body-state reading
- `limbic_perceive`: appraise an event and nudge a drive
- `limbic_touch`: map a configured interaction kind onto drive changes
- `limbic_safeword`: immediately dampen one or all drives

The engine should inform expression, never bypass the identity's values,
boundaries, judgment, or the human participant's consent.
""",
}


def _name_pattern(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])",
        re.IGNORECASE,
    )


NAME_SCRUBS = [
    (_name_pattern(private), public)
    for private, public in PRIVATE_NAMES.items()
]
IDENTIFIER_SCRUBS = [
    (re.compile(rf"{re.escape(private)}(?=[A-Z_])"), public)
    for private, public in PRIVATE_NAMES.items()
] + [
    (
        re.compile(rf"(?<![A-Za-z]){re.escape(private.lower())}(?=[A-Z_])"),
        public.lower(),
    )
    for private, public in PRIVATE_NAMES.items()
] + [
    (re.compile(rf"(?<=[a-z0-9_]){re.escape(private)}(?![a-z])"), public)
    for private, public in PRIVATE_NAMES.items()
]

FORBIDDEN_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-|ant-|or-)?[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"eyJhbGciOi"),
    re.compile(r"\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}\b"),
]
FORBIDDEN_PATTERNS.extend(_name_pattern(name) for name in PRIVATE_NAMES)
FORBIDDEN_PATTERNS.extend(pattern for pattern, _ in IDENTIFIER_SCRUBS)

_NUMERIC_ID_PATTERN = re.compile(r"\b\d{17,20}\b")
_PUBLIC_NUMERIC_IDS: dict[str, str] = {}


def _replace_numeric_id(match: re.Match[str]) -> str:
    """Keep public ID relationships intact without retaining private IDs."""
    private_id = match.group(0)
    if set(private_id) == {"0"} or private_id.startswith("900000000000000"):
        return private_id
    if private_id not in _PUBLIC_NUMERIC_IDS:
        _PUBLIC_NUMERIC_IDS[private_id] = f"9{len(_PUBLIC_NUMERIC_IDS) + 1:017d}"
    return _PUBLIC_NUMERIC_IDS[private_id]


def should_include(project: str, relative: Path) -> bool:
    posix = PurePosixPath(relative.as_posix())
    parts = set(posix.parts)
    if parts & COMMON_EXCLUDED_DIRS:
        return False
    if relative.name in COMMON_EXCLUDED_NAMES:
        return False
    if relative.suffix.lower() in COMMON_EXCLUDED_SUFFIXES:
        return False
    if relative.name.startswith("tmp") or relative.suffix.lower() in {".tmp", ".jpg", ".png", ".mp3", ".wav"}:
        return False
    # Hand-made backups (index.ts.bak-merge-2026-08-22, README.md.bak-...)
    # are not text-suffixed, so they were copied RAW and unscrubbed -- ten of
    # them leaked names into the public folder on 2026-09-01. Skip them.
    if any(marker in relative.name.lower() for marker in (".bak-", ".bak", ".prepatch", ".pre-", ".orig")):
        return False
    if relative.name.startswith(".env") and relative.name != ".env.example":
        return False
    if relative.name.startswith(".dev.vars") and relative.name != ".dev.vars.example":
        return False

    if project == "hearth-hub":
        if posix.parts and posix.parts[0] == "scripts":
            return False
        if relative.name == "seed_data.py":
            return False
        if (
            posix.parts
            and posix.parts[0] == "migrations"
            and relative.name not in {
                "0001_initial.sql",
                "0002_add_projects_games_notes_birthdays.sql",
            }
        ):
            return False

    if project == "machine-agent":
        if len(posix.parts) >= 2 and posix.parts[:2] == ("cloudflare", "generated"):
            return False
        if relative.as_posix() == "configs/server-urls.md":
            return False

    if project == "google-cloud-mcp":
        return (posix.parts[0] in {"src", "migrations"}
                or relative.as_posix() == "scripts/google-oauth.mjs"
                or relative.name in {"package.json", "package-lock.json", "tsconfig.json", "wrangler.toml", ".gitignore"})

    if project in {"hearth-commons", "elevenlabs-mcp"}:
        return (posix.parts[0] == "src"
                or relative.name in {"package.json", "package-lock.json", "tsconfig.json", "wrangler.toml", ".gitignore", ".dev.vars.example"})

    if project == "mind-backend":
        allowed_root_files = {
            ".gitignore", "package.json", "package-lock.json", "README.md", "COORDINATED-MEMORY.md",
            "tsconfig.json", "wrangler.toml",
        }
        if len(posix.parts) == 1:
            return relative.name in allowed_root_files
        if posix.parts[0] in {"src", "migrations"}:
            return True
        if posix.parts[0] == "scripts":
            return relative.name in {
                "README.md", "retrieval_benchmark.py",
                "retrieval_eval_set.example.json", "text_normalize.py", "anam-reader.test.mjs", "cognition.test.mjs", "sketchbook.test.mjs",
            }
        return False

    if project == "limbic":
        allowed_root_files = {
            ".dev.vars.example", ".gitattributes", ".gitignore",
            "package.json", "package-lock.json", "README.md",
            "tsconfig.json", "wrangler.toml",
        }
        if len(posix.parts) == 1:
            return relative.name in allowed_root_files
        if posix.parts[0] == "src":
            return True
        if posix.parts[0] == "migrations":
            return relative.name == "0001_limbic_init.sql"
        return False

    return True


def scrub_text(text: str) -> tuple[str, int]:
    replacements = 0
    # Private paths can occur as raw, JSON-escaped, or forward-slash strings.
    for private, public in sorted(PRIVATE_NAMES.items(), key=lambda item: -len(item[0])):
        if "\\" in private or "/" in private or "." in private:
            for variant in {private, private.replace("\\", "/"), private.replace("\\", "\\\\")}:
                text, count = re.subn(re.escape(variant), lambda _: public, text, flags=re.I)
                replacements += count
    for pattern, replacement in NAME_SCRUBS:
        def replace_name(match: re.Match[str], public: str = replacement) -> str:
            original = match.group(0)
            if original.isupper():
                return public.upper()
            if original.islower():
                return public.lower()
            return public

        text, count = pattern.subn(replace_name, text)
        replacements += count
    for pattern, replacement in IDENTIFIER_SCRUBS:
        text, count = pattern.subn(replacement, text)
        replacements += count

    fixed_scrubs = [
        (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "you@example.com"),
        (
            re.compile(r"https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev", re.I),
            "https://YOUR-WORKER.YOUR-ACCOUNT.workers.dev",
        ),
        (re.compile(r"(?<!googleapis\.com)/(mcp|auth)/[A-Za-z0-9._~-]{12,}"), r"/\1/YOUR-SECRET"),
        (
            re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I),
            "00000000-0000-0000-0000-000000000000",
        ),
        (re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "192.168.1.100"),
        (
            re.compile(r"(?i)((?:voiceId|voice_id|ELEVENLABS_VOICE_ID)[\"']?\s*[:=]\s*[\"'])[^\"']+"),
            r"\1YOUR-VOICE-ID",
        ),
        (
            re.compile(r"(?i)([\"']vendorId[\"']\s*:\s*[\"'])[^\"']+"),
            r"\1YOUR-ALEXA-VENDOR-ID",
        ),
        (
            re.compile(r"(?i)([\"']uri[\"']\s*:\s*[\"'])https://[^\"']+"),
            r"\1https://YOUR-WORKER.YOUR-ACCOUNT.workers.dev",
        ),
        (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1YOUR-TOKEN"),
        (re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-|ant-|or-)?[A-Za-z0-9_-]{12,}"), "YOUR-API-KEY"),
    ]
    for pattern, replacement in fixed_scrubs:
        text, count = pattern.subn(replacement, text)
        replacements += count
    text, count = _NUMERIC_ID_PATTERN.subn(_replace_numeric_id, text)
    replacements += count

    # Uppercase deployment variables in TOML/JSON examples should never retain
    # literal credentials, even when their names are project-specific.
    config_secret = re.compile(
        r"(?im)^(\s*[\"']?[A-Z0-9_]*(?:SECRET|TOKEN|API_KEY|PASSWORD|PRIVATE_KEY)"
        r"[A-Z0-9_]*[\"']?\s*[:=]\s*)([\"'])([^\"']*)(\2)"
    )

    def replace_config_secret(match: re.Match[str]) -> str:
        value = match.group(3)
        if not value or re.search(r"(?i)(your-|example|replace|placeholder|test[-_]|dummy|mock|fake)", value) or re.fullmatch(r"[A-Z][A-Z0-9_]+", value):
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}YOUR-SECRET{match.group(2)}"

    text, count = config_secret.subn(replace_config_secret, text)
    replacements += count
    return text, replacements


def copy_file(src: Path, dst: Path, dry_run: bool) -> int:
    if dry_run:
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    lower_name = src.name.lower()
    is_env_template = (
        ".env." in lower_name
        or lower_name.endswith(".env")
        or lower_name.startswith(".dev.vars")
    )
    if (
        src.suffix.lower() in TEXT_SUFFIXES
        or src.name.startswith(".env")
        or is_env_template
        or src.name.upper() in {"LICENSE", "NOTICE"}
    ):
        try:
            text = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copy2(src, dst)
            return 0
        text, replacements = scrub_text(text)
        dst.write_text(text, encoding="utf-8")
        shutil.copystat(src, dst, follow_symlinks=False)
        return replacements
    shutil.copy2(src, dst)
    return 0


def build_jobs(projects: set[str] | None = None) -> list[tuple[str, Path, Path]]:
    jobs: list[tuple[str, Path, Path]] = []
    for project, source_root in PROJECT_SOURCES.items():
        if projects is not None and project not in projects:
            continue
        if not source_root.exists():
            raise FileNotFoundError(f"Missing source for {project}: {source_root}")
        for source in source_root.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(source_root)
            if should_include(project, relative):
                jobs.append((project, source, DEST_ROOT / project / relative))
        if project == "limbic":
            for source in sorted((source_root / "migrations").glob("*.sql")):
                if source.name != "0001_limbic_init.sql" and re.search(r"CREATE TABLE IF NOT EXISTS recipes", source.read_text(encoding="utf-8")):
                    jobs.append((project, source, DEST_ROOT / project / "migrations" / "0002_recipes_schema.sql"))
    for source in sorted(OVERRIDES_ROOT.rglob("*")):
        if source.is_file():
            relative = source.relative_to(OVERRIDES_ROOT)
            project = relative.parts[0]
            if project in PROJECT_SOURCES and (projects is None or project in projects):
                jobs.append((project, source, DEST_ROOT / relative))
    return jobs


def remove_stale_files(
    jobs: list[tuple[str, Path, Path]],
    dry_run: bool,
    projects: set[str],
) -> int:
    if not DEST_ROOT.exists():
        return 0
    expected = {destination.resolve() for _, _, destination in jobs}
    expected.update(
        (DEST_ROOT / project / "README.md").resolve()
        for project in PROJECT_READMES
        if project in projects
    )
    removed = 0
    for project in projects:
        project_root = DEST_ROOT / project
        if not project_root.exists():
            continue
        for path in project_root.rglob("*"):
            if path.is_file() and path.resolve() not in expected:
                if not dry_run:
                    path.unlink()
                removed += 1
    if not dry_run:
        for directory in sorted(
            (path for path in DEST_ROOT.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
    return removed


def write_catalog(dry_run: bool, projects: set[str]) -> None:
    if dry_run:
        return
    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    catalog = """# Shareable Cloud Setups

Public-safe starter copies of the local cloud services. These folders contain
application code and setup documentation, never the original deployment state.

| Folder | Purpose |
|---|---|
| `alexa-skill` | Alexa custom-skill Worker examples |
| `discord-backend` | Stateless Discord MCP Worker |
| `easel-ai` | Local-first image gallery and prompt workshop |
| `elevenlabs-mcp` | ElevenLabs text-to-speech MCP Worker (voice generation for ChatGPT / MCP clients) |
| `google-cloud-mcp` | Google Drive, Docs, Sheets, Calendar, YouTube, and optional Health MCP Worker |
| `hearth-hub` | D1-backed shared-space/state service |
| `hearth-commons` | Remote MCP connector for a shared Commons server |
| `limbic` | Advisory emotional-state MCP Worker |
| `machine-agent` | Narrow local-machine bridge and cloud proxy scaffolds |
| `mind-backend` | D1-backed memory, continuity, and retrieval service |
| `social-backend` | Social and world-tool MCP Worker |
| `shared-docs` | Generic cross-service architecture notes |

## Safety boundary

Excluded from every export: `.env`, `.dev.vars`, token files, local databases,
logs, Cloudflare state, private import SQL, personal seed data, and captured
validation output. Wrangler IDs, private hosts, account names, owner/companion
names, local paths, bot IDs, voice IDs, and literal secrets are replaced with
obvious `YOUR-*` placeholders.

Run `export_shareable_setups.py` from the collection root with
`CLOUD_SETUP_SOURCE`, `MACHINE_AGENT_SOURCE`, `COMMONS_WORKER_SOURCE`,
`ELEVENLABS_WORKER_SOURCE`, `LIMBIC_SETUP_SOURCE`, and
`SHARE_PRIVATE_REPLACEMENTS` supplied by your private environment whenever the
source workspace changes. Set `CLOUD_SETUP_DEST` only when the tracked public
tree lives somewhere other than `setup/cloud-setups`.
"""
    if projects == set(PROJECT_SOURCES):
        (DEST_ROOT / "README.md").write_text(catalog, encoding="utf-8")
        (DEST_ROOT / ".gitignore").write_text(
            "node_modules/\n.env\n.env.local\n.dev.vars\n.wrangler/\n"
            "data/\nlogs/\n*.db\n*.sqlite*\n__pycache__/\n",
            encoding="utf-8",
        )
    for project, readme in PROJECT_READMES.items():
        if project not in projects:
            continue
        if (OVERRIDES_ROOT / project / "README.md").exists():
            continue
        (DEST_ROOT / project).mkdir(parents=True, exist_ok=True)
        (DEST_ROOT / project / "README.md").write_text(readme, encoding="utf-8")


def postprocess_docs(dry_run: bool, projects: set[str]) -> None:
    """Public templates and adapt.py own all installation-specific differences."""
    return


def verify_public_copy() -> list[str]:
    problems: list[str] = []
    forbidden_filenames = COMMON_EXCLUDED_NAMES | {"seed_data.py"}
    for path in DEST_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(DEST_ROOT)
        if path.name in forbidden_filenames:
            problems.append(f"{relative}: private runtime/data filename")
            continue
        if (
            len(relative.parts) >= 3
            and relative.parts[:2] == ("hearth-hub", "migrations")
            and relative.name not in {
                "0001_initial.sql",
                "0002_add_projects_games_notes_birthdays.sql",
            }
        ):
            problems.append(f"{relative}: private seed migration")
            continue
        if any(part in COMMON_EXCLUDED_DIRS for part in relative.parts):
            problems.append(f"{relative}: excluded runtime directory")
            continue
        lower_name = path.name.lower()
        is_env_template = (
            ".env." in lower_name
            or lower_name.endswith(".env")
            or lower_name.startswith(".dev.vars")
        )
        if (
            path.suffix.lower() not in TEXT_SUFFIXES
            and not path.name.startswith(".env")
            and not is_env_template
            and path.name.upper() not in {"LICENSE", "NOTICE"}
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in FORBIDDEN_PATTERNS:
            unsafe = False
            for match in pattern.finditer(text):
                sample = match.group(0).lower()
                if any(marker in sample for marker in ("your-key", "supersecretvalue", "example-secret")):
                    continue
                unsafe = True
                break
            if unsafe:
                problems.append(f"{relative}: personal/credential pattern")
                break
    return problems


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--project",
        action="append",
        choices=sorted(PROJECT_SOURCES),
        help="Export only this setup (repeatable). Defaults to all setups.",
    )
    args = parser.parse_args()

    if not PRIVATE_NAMES:
        print(
            "SHARE_PRIVATE_REPLACEMENTS is required so private owner/identity "
            "labels cannot silently pass through."
        )
        return 2

    projects = set(args.project or PROJECT_SOURCES)
    jobs = build_jobs(projects)
    removed = remove_stale_files(jobs, args.dry_run, projects)
    copied = 0
    replacements = 0
    for _, source, destination in jobs:
        replacements += copy_file(source, destination, args.dry_run)
        copied += 1
    write_catalog(args.dry_run, projects)
    postprocess_docs(args.dry_run, projects)
    if not args.dry_run:
        runpy.run_path(str(OVERRIDES_ROOT / "adapt.py"))["adapt"](DEST_ROOT, projects)

    action = "Would copy" if args.dry_run else "Copied"
    print(
        f"{action} {copied} files across {len(projects)} setups "
        f"({replacements} replacements, {removed} stale files removed)"
    )
    if args.dry_run:
        return 0

    problems = verify_public_copy()
    if problems:
        print("\nPUBLIC-DATA CHECK FAILED:")
        for problem in problems[:40]:
            print(f"  !! {problem}")
        return 1
    print("Public-data check: CLEAN. Cloud setup copies contain no known personal details or secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
