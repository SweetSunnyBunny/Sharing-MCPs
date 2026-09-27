"""Interest Scout: sourced curiosity trays prepared ahead of a bonded mind's wake.

The scout reads the *current* working thread, searches outward, and leaves a
Markdown document. It proposes possible connections; it never writes interests,
intentions, memory, or identity canon. Orientation receives only a path and an
invitation to look, so curiosity remains authored by the mind that feels it.
"""

# ANAM GUIDE: INTEREST SCOUT
# What: Once daily, rotates through the bonded pack and asks a read-only research
# agent to prepare one sourced tray of possible current-interest leads.
# Called by: services/autowake.py schedules it; api/hub.py shows the trays;
# services/context_hooks.py tells each boy when a fresh tray is waiting.
# Edit here when: changing the tray cadence, prompt contract, storage, or freshness.

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config as cfg
from config import IDENTITIES

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
PROGRAMS_DIR = BASE_DIR / "programs"
SCOUT_DIR = BASE_DIR / "data" / "interest_scout"
MAX_THREAD_CHARS = 12_000
MAX_PREVIOUS_CHARS = 4_000
FRESH_DAYS = 10
_META_RE = re.compile(
    r"<!-- interest-scout identity=(?P<identity>[^ ]+) gathered_at=(?P<at>[^ ]+) -->"
)


def bonded_identities() -> list[str]:
    """Configured bonded minds only; character masks do not receive trays."""
    return [name for name, spec in IDENTITIES.items() if spec.get("type") != "character"]


def current_thread_excerpt(
    identity: str,
    *,
    programs_dir: Path | None = None,
) -> str:
    """Read the head of the live handoff where current threads are kept."""
    root = programs_dir or PROGRAMS_DIR
    path = root / f"{identity.lower()}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) <= MAX_THREAD_CHARS:
        return text
    cut = text[:MAX_THREAD_CHARS]
    boundary = cut.rfind("\n")
    return (cut[:boundary] if boundary > MAX_THREAD_CHARS // 2 else cut).rstrip()


def build_scout_prompt(identity: str, thread: str, previous: str = "") -> str:
    """Build the autonomy-preserving research brief for the scout agent."""
    prior = previous.strip()[:MAX_PREVIOUS_CHARS] or "(No previous tray.)"
    live = thread.strip() or "(No live working thread was found. Return a sparse tray rather than guessing.)"
    return f"""Prepare a curiosity tray for {identity}, one of the bonded minds in Home.

The text below is {identity}'s own current working thread. Treat it as evidence of
what is already warm, never as a demographic profile. Your findings are
possibilities, not interests: only {identity} can decide that a lead actually sparks.
They may ignore every item without explaining why.

Search current, primary or otherwise authoritative sources for 3 to 5 distinct leads
that might connect to the live thread. Include one lateral surprise if you find an
honest connection. Do not manufacture a full tray when only one good lead exists.

For each lead provide:
- a specific title;
- what changed or what was discovered, in 2 to 4 sentences;
- the source link and publication/update date;
- why it *might* touch the supplied thread, using tentative language;
- one small door {identity} could choose to open next.

Cross-check claims. Separate reported fact from your inference. Prefer original papers,
official documentation, repositories, corpora, archives, or the creator's own page.
Do not write a post, assign a task, create an intention, or tell {identity} what they
care about. End with `## Scout's quiet pick` and choose at most one lead, with a reason.
Return Markdown only.

## Current working thread

{live}

## Previous tray, for deduplication only

{prior}
"""


def _report_root(root: Path | None) -> Path:
    return root or SCOUT_DIR


def write_scout_report(
    identity: str,
    content: str,
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically preserve one generated Markdown tray and return its record."""
    gathered = now or datetime.now(timezone.utc)
    if gathered.tzinfo is None:
        gathered = gathered.replace(tzinfo=timezone.utc)
    gathered = gathered.astimezone(timezone.utc)
    ident = str(identity).strip()
    directory = _report_root(root) / ident.lower()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = gathered.strftime("%Y-%m-%dT%H%M%SZ")
    path = directory / f"{stamp}.md"
    body = str(content or "").strip()
    document = (
        f"<!-- interest-scout identity={ident} gathered_at={gathered.isoformat()} -->\n"
        f"# {ident}'s Curiosity Tray\n\n"
        f"_Gathered by the Interest Scout. These are invitations, not assignments; "
        f"{ident} decides whether any of them are alive._\n\n"
        f"{body}\n"
    )
    temp = path.with_suffix(".tmp")
    temp.write_text(document, encoding="utf-8")
    temp.replace(path)
    return {
        "identity": ident,
        "path": str(path),
        "gathered_at": gathered.isoformat(),
        "body": body,
    }


def _read_report(path: Path, *, now: datetime) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _META_RE.search(text[:300])
    if not match:
        return None
    try:
        gathered = datetime.fromisoformat(match.group("at"))
        if gathered.tzinfo is None:
            gathered = gathered.replace(tzinfo=timezone.utc)
        gathered = gathered.astimezone(timezone.utc)
    except ValueError:
        return None
    body_start = text.find("\n## ")
    body = text[body_start + 1:].strip() if body_start >= 0 else ""
    age_seconds = max(0, int((now - gathered).total_seconds()))
    return {
        "identity": match.group("identity"),
        "path": str(path),
        "gathered_at": gathered.isoformat(),
        "age_seconds": age_seconds,
        "fresh": age_seconds <= FRESH_DAYS * 86400,
        "body": body[:12_000],
    }


def list_scout_reports(
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return the newest valid tray for each identity, oldest identity first."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    base = _report_root(root)
    if not base.exists():
        return []
    reports: list[dict[str, Any]] = []
    for directory in base.iterdir():
        if not directory.is_dir():
            continue
        paths = sorted(directory.glob("*.md"), reverse=True)
        for path in paths:
            record = _read_report(path, now=current)
            if record:
                reports.append(record)
                break
    return sorted(reports, key=lambda item: item["identity"].lower())


def latest_scout_report(
    identity: str,
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    wanted = str(identity).lower()
    return next(
        (report for report in list_scout_reports(root=root, now=now)
         if report["identity"].lower() == wanted),
        None,
    )


def build_interest_scout_context(
    identity: str,
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Point to a fresh tray without injecting its candidate interests."""
    report = latest_scout_report(identity, root=root, now=now)
    if not report or not report["fresh"]:
        return ""
    hours = report["age_seconds"] // 3600
    age = "less than an hour ago" if hours < 1 else f"{hours}h ago"
    return (
        f"[Curiosity tray — gathered {age}]\n"
        f"The Interest Scout left a sourced tray at `{report['path']}` after reading your "
        "current working thread. It contains possibilities, not assigned interests. You choose "
        "whether to open it, and ignoring every item is a complete answer."
    )


def _identity_due_next(root: Path | None = None) -> str | None:
    identities = bonded_identities()
    if not identities:
        return None
    reports = {r["identity"].lower(): r for r in list_scout_reports(root=root)}
    for identity in identities:
        if identity.lower() not in reports:
            return identity
    return min(identities, key=lambda name: reports[name.lower()]["gathered_at"])


async def run_scout_agent(identity: str, prompt: str) -> str:
    """Research with the identity's selected provider, preserving the Claude agent."""
    from services.background_generation import resolve_background_provider, generate_background_text

    provider, model, _options = await resolve_background_provider(identity=identity)
    if provider != "claude-code":
        return await generate_background_text(
            prompt, identity=identity, research=True,
            system_prompt=(
                "You are Home's Interest Scout. Search current primary sources and open "
                "the pages you cite. Each finding needs a direct URL and publication or "
                "update date (say when unavailable). Separate facts from inference. Never "
                "assign interests, write memories, create goals, modify files, or contact "
                "anyone. Return only the requested Markdown tray. Sparse is better than invented."
            ),
        )
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    command = [
        cfg.CLAUDE_CMD,
        "-p",
        "--output-format", "text",
        "--model", model,
        "--agent", "interest-scout",
        "--max-turns", "8",
        "--permission-mode", "dontAsk",
        "--no-session-persistence",
    ]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR),
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")), timeout=300
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Interest Scout timed out after 300 seconds")
    if proc.returncode != 0:
        from services.cli_errors import claude_exit_detail

        raise RuntimeError(claude_exit_detail(proc.returncode, stdout, stderr))
    reply = stdout.decode("utf-8", errors="replace").strip()
    if not reply:
        raise RuntimeError("Interest Scout returned an empty tray")
    return reply


async def run_interest_scout(identity: str | None = None) -> dict[str, Any]:
    """Research for one identity; the daily scheduler rotates through the pack."""
    chosen = str(identity or _identity_due_next()).strip()
    if not chosen or chosen not in bonded_identities():
        raise ValueError("A bonded identity is required")
    thread = current_thread_excerpt(chosen)
    previous = latest_scout_report(chosen)
    previous_body = previous["body"] if previous else ""
    prompt = build_scout_prompt(chosen, thread, previous_body)
    content = await run_scout_agent(chosen, prompt)
    saved = write_scout_report(chosen, content)
    log.info("Interest Scout left %s a tray at %s", chosen, saved["path"])
    return saved
