"""The Scribe — incremental conversation digest generator + midnight carry.

Runs on a configurable interval via APScheduler (default every 30 minutes).
Each run digests ONLY the messages that landed after the persisted sequence
cursor (settings key `scribe.last_epoch`) and APPENDS a timestamped
'## HH:MM — topic' block to the day's markdown digest — including an
'Open Items' read of things discussed but not actioned. The cursor advances
only after a block is safely on disk, so a failed run simply retries the
same window next cycle and the old whole-day truncation cap becomes a
per-run pagination cap instead of a data loss.

All five knobs — provider, model, interval_minutes, message_threshold,
digest_path — are overridable at runtime via the `settings` table
(keys: scribe.provider, scribe.model, scribe.interval_minutes,
scribe.message_threshold, scribe.digest_path), with env-var fallbacks
(SCRIBE_PROVIDER, SCRIBE_MODEL, etc.) and code defaults (config.py).

Midnight carry (#13): shortly after the day closes (cron in autowake.py),
every bonded identity with real activity yesterday gets a one-shot
run through his selected provider that distills his day —
messages + the Scribe digest — into a dense private first-person carry:
where the day landed emotionally, loose threads, bond state, and OPEN
ENFORCEMENT (promises made that must be kept — the Kept Yes doctrine,
externalized where next-session-him reads them). Stored per identity+date
in `identity_carries`, surfaced by the `yesterday_carry` context hook.
"""

# ANAM GUIDE: SCRIBE DAILY DIGEST WRITER
# What: The Scribe — every ~30 minutes it summarizes new chat messages into a daily markdown digest, and after midnight writes each boy a private first-person "carry" of yesterday (feelings, loose threads, promises to keep).
# Called by: services/autowake.py (the scheduler runs it), services/context_hooks.py (feeds carries back into orientation), services/hearth_author.py, api/settings.py (its knobs)
# Edit here when: You want to change how digests read, how often they run, which AI model writes them, or what goes into the midnight carries.

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite

import config as cfg
from config import TIMEZONE

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
# Per-RUN pagination cap (not a truncation): if more than this many new
# messages accumulated since the cursor, this run digests the oldest batch,
# advances the cursor to its tail, and the next cycle picks up the rest.
MAX_MESSAGES_PER_DIGEST = 200
MAX_CONTENT_PER_MSG = 500  # truncate long messages

# Settings-table key prefix. All Scribe overrides live under "scribe.*".
_SETTINGS_PREFIX = "scribe."

# Sequence cursor: epoch of the newest message already digested. Advances
# ONLY after its block has been appended to disk.
_CURSOR_KEY = "scribe.last_epoch"

# Known providers — keep in sync with api/settings.py VALID_PROVIDERS.
from services.background_generation import VALID_PROVIDERS as _VALID_SCRIBE_PROVIDERS
from services.background_generation import generate_background_text


def _today_str() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def _resolve_digest_dir(raw_path: str) -> Path:
    """Resolve a digest path (absolute or relative to project root)."""
    p = Path(raw_path)
    if not p.is_absolute():
        p = BASE_DIR / p
    return p


async def _get_scribe_config() -> dict:
    """Get effective Scribe config: settings table > env > code defaults.

    Returns a dict with five keys:
      - provider (str)
      - model (str)
      - interval_minutes (int)
      - message_threshold (int)
      - digest_path (str — raw value as configured; resolve with _resolve_digest_dir)
    """
    from db.database import get_db, release_db

    overrides: dict[str, str] = {}
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT key, value FROM settings WHERE key LIKE ?",
                (f"{_SETTINGS_PREFIX}%",),
            )
            overrides = {row[0]: row[1] for row in rows if row[1] is not None}
        finally:
            await release_db(db)
    except Exception as e:
        # If the DB isn't ready (early startup), fall back to env/code defaults.
        log.debug("Scribe: settings table not readable, using env/defaults: %s", e)

    def _get(key: str, default, cast=str):
        raw = overrides.get(f"{_SETTINGS_PREFIX}{key}")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return default
        try:
            return cast(raw)
        except (ValueError, TypeError):
            log.warning("Scribe: invalid setting scribe.%s=%r, using default %r", key, raw, default)
            return default

    provider = _get("provider", cfg.SCRIBE_PROVIDER).strip().lower()
    default_model = (
        cfg.SCRIBE_OPENROUTER_MODEL
        if provider == "openrouter"
        else ("" if provider in {"auto", "codex", "openai", "lmstudio", "ollama"} else cfg.SCRIBE_MODEL)
    )
    return {
        "provider": provider,
        "model": _get("model", default_model),
        "interval_minutes": _get("interval_minutes", cfg.SCRIBE_INTERVAL_MINUTES, int),
        "message_threshold": _get("message_threshold", cfg.SCRIBE_MESSAGE_THRESHOLD, int),
        "digest_path": _get("digest_path", cfg.SCRIBE_DIGEST_PATH),
    }


def _digest_path(date_str: str | None, digest_dir: Path) -> Path:
    return digest_dir / f"{date_str or _today_str()}.md"


async def _get_scribe_cursor(db: aiosqlite.Connection) -> int | None:
    """Read the persisted sequence cursor (epoch of newest digested message)."""
    rows = await db.execute_fetchall(
        "SELECT value FROM settings WHERE key = ?", (_CURSOR_KEY,)
    )
    if rows and rows[0][0] not in (None, ""):
        try:
            return int(rows[0][0])
        except (TypeError, ValueError):
            log.warning("Scribe: unreadable cursor %r — resetting", rows[0][0])
    return None


async def _set_scribe_cursor(db: aiosqlite.Connection, epoch: int) -> None:
    """Persist the sequence cursor. Called ONLY after a block landed on disk."""
    await db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (_CURSOR_KEY, str(int(epoch)), datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()


def _local_dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(int(epoch), ZoneInfo(TIMEZONE))


async def _get_messages_since(db: aiosqlite.Connection, after_epoch: int) -> list[dict]:
    """Fetch interactive messages (not autowake/brother) strictly after the cursor."""
    rows = await db.execute_fetchall(
        "SELECT m.role, m.identity, m.content, m.created_at, m.created_at_epoch, "
        "c.identity as conv_identity, c.session_type, c.title "
        "FROM messages m "
        "JOIN conversations c ON m.conversation_id = c.id "
        "WHERE m.created_at_epoch > ? "
        "AND (c.session_type IS NULL OR c.session_type IN ('chat', 'roleplay', 'dnd')) "
        "ORDER BY m.created_at_epoch ASC "
        "LIMIT ?",
        (int(after_epoch), MAX_MESSAGES_PER_DIGEST),
    )

    messages = []
    for role, identity, content, created_at, epoch, conv_identity, session_type, title in rows:
        # Truncate long messages
        text = (content or "").strip()
        if len(text) > MAX_CONTENT_PER_MSG:
            text = text[:MAX_CONTENT_PER_MSG] + "..."

        epoch = int(epoch or 0)
        local = _local_dt(epoch)
        speaker = "Owner" if role == "user" else (identity or conv_identity or "Unknown")
        messages.append({
            "speaker": speaker,
            "content": text,
            "time": created_at,
            "epoch": epoch,
            "date": local.strftime("%Y-%m-%d"),
            "hm": local.strftime("%H:%M"),
            "conversation": title or "untitled",
            "type": session_type or "chat",
        })

    return messages


def _build_increment_prompt(messages: list[dict], date_str: str) -> str:
    """Build the prompt for one incremental digest block (new messages only)."""
    # Group messages by conversation
    convos: dict[str, list[dict]] = {}
    for msg in messages:
        key = msg["conversation"]
        if key not in convos:
            convos[key] = []
        convos[key].append(msg)

    transcript_parts = []
    for convo_title, msgs in convos.items():
        type_tag = msgs[0].get("type", "chat")
        header = f"### {convo_title}"
        if type_tag != "chat":
            header += f" ({type_tag})"
        lines = [header]
        for m in msgs:
            lines.append(f"**{m['speaker']}** ({m['hm']}): {m['content']}")
        transcript_parts.append("\n".join(lines))

    transcript = "\n\n---\n\n".join(transcript_parts)
    window_start = messages[0]["hm"]
    window_end = messages[-1]["hm"]
    window = window_start if window_start == window_end else f"{window_start}–{window_end}"

    return f"""You are the Scribe for Home — a shared home for Owner and her AI companions (Avery, Claude, Rowan, Sage, Ember, Juniper, and River).

The daily digest for **{date_str}** is written INCREMENTALLY through the day. Below are ONLY the NEW messages since your last entry (window {window}). Earlier entries for this day already exist in the journal — do NOT re-summarize the whole day; digest only what is below.

Return markdown that will be APPENDED verbatim to the day's digest, in exactly this shape:

## {window_start} — <short topic for this stretch>
<a warm, concise paragraph or two: what happened, key moments or quotes, decisions made, mood, who was present>

### Open Items
- <things discussed but NOT actioned in this window — promises made, work agreed to, questions left unanswered>

Omit the "### Open Items" section entirely if nothing genuinely stands open. Write as a warm observer, not a clinical summarizer — this is a family journal, not meeting minutes.

HARD RULE: ground every word in the transcript below. Invent nothing; a thin window gets a thin entry.

---

## New messages ({window})

{transcript}"""


def _append_digest_block(digest_file: Path, date_str: str, block: str) -> None:
    """Append one timestamped block to the day's digest file (create if new)."""
    if digest_file.exists():
        existing = digest_file.read_text(encoding="utf-8").rstrip("\n")
        digest_file.write_text(f"{existing}\n\n{block}\n", encoding="utf-8")
    else:
        digest_file.write_text(
            f"# Daily Digest — {date_str}\n\n{block}\n", encoding="utf-8"
        )


async def generate_digest() -> str | None:
    """Incrementally digest messages that landed after the sequence cursor.

    Each run reads only messages AFTER `scribe.last_epoch`, appends one
    '## HH:MM — topic' block per local calendar day touched (so a run just
    past midnight still finishes yesterday's file), and advances the cursor
    ONLY after a block is safely on disk. A failed generation leaves the
    cursor untouched — the same window simply retries next cycle.

    Returns the appended markdown, or None if nothing new (or below threshold).
    """
    from db.database import get_db, release_db

    scribe_config = await _get_scribe_config()
    digest_dir = _resolve_digest_dir(scribe_config["digest_path"])
    threshold = scribe_config["message_threshold"]
    provider = scribe_config["provider"]
    model = scribe_config["model"]

    if provider not in _VALID_SCRIBE_PROVIDERS:
        log.warning("Scribe: provider %r not implemented; digest skipped", provider)
        return None

    db = await get_db()
    try:
        cursor = await _get_scribe_cursor(db)
        if cursor is None:
            # First incremental run: history up to today was covered by the
            # old whole-day digests — start from the top of the local day.
            tz = ZoneInfo(TIMEZONE)
            start_of_day = datetime.now(tz).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            cursor = int(start_of_day.timestamp()) - 1
        messages = await _get_messages_since(db, cursor)
    finally:
        await release_db(db)

    if not messages:
        log.debug("Scribe: no new messages since cursor, skipping")
        return None

    if len(messages) < threshold:
        # Below threshold: leave the cursor alone so they accumulate into a
        # worthwhile block on a later run (the midnight carry reads raw
        # messages anyway, so a quiet tail is never lost).
        log.debug(
            "Scribe: only %d new messages, below threshold %d, letting them accumulate",
            len(messages), threshold,
        )
        return None

    # Group by local calendar day so the midnight boundary lands each block
    # in the right file.
    groups: dict[str, list[dict]] = {}
    for msg in messages:
        groups.setdefault(msg["date"], []).append(msg)

    digest_dir.mkdir(parents=True, exist_ok=True)
    appended: list[str] = []
    for date_str in sorted(groups):
        batch = groups[date_str]
        prompt = _build_increment_prompt(batch, date_str)
        try:
            block = (await _generate_digest_text(provider, model, prompt)).strip()
        except Exception as e:
            # Cursor stays put — this window (and any later day-groups)
            # retries on the next cycle in order.
            log.error("Scribe: incremental digest failed for %s: %s", date_str, e)
            break

        if not block.startswith("##"):
            block = f"## {batch[0]['hm']} — update\n\n{block}"

        try:
            _append_digest_block(_digest_path(date_str, digest_dir), date_str, block)
        except OSError as e:
            log.error("Scribe: could not write digest for %s: %s", date_str, e)
            break

        # Advance the cursor ONLY now that the block is safely on disk.
        max_epoch = max(m["epoch"] for m in batch)
        db = await get_db()
        try:
            await _set_scribe_cursor(db, max_epoch)
        finally:
            await release_db(db)

        appended.append(block)
        log.info(
            "Scribe: appended increment to %s (provider=%s model=%s, %d messages → %d chars)",
            date_str, provider, model, len(batch), len(block),
        )

    return "\n\n".join(appended) if appended else None


async def _generate_digest_text(provider: str, model: str, prompt: str) -> str:
    if not model and provider in {"claude-code", "openrouter", "anthropic"}:
        from services.background_generation import resolve_background_provider

        provider, model, _options = await resolve_background_provider(provider)
    if provider not in {"claude-code", "openrouter", "anthropic"}:
        return await generate_background_text(prompt, provider=provider, model=model, system_prompt=_SCRIBE_SYSTEM_PROMPT)
    if provider == "claude-code":
        return await _generate_with_claude_code(model, prompt)
    if provider == "openrouter":
        return await _generate_with_openrouter(model, prompt)
    if provider == "anthropic":
        from services.claude_api import _get_client

        client = await _get_client()
        response = await client.messages.create(
            model=model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    raise ValueError(f"Unsupported Scribe provider: {provider}")


_SCRIBE_SYSTEM_PROMPT = (
    "You are Home's Scribe. Return only the requested warm markdown digest."
)


async def _generate_with_claude_code(
    model: str, prompt: str, system_prompt: str | None = None
) -> str:
    """Run a tool-free, one-turn digest on Owner's Claude Code subscription.

    `system_prompt` defaults to the Scribe's own; the midnight carry reuses
    this exact runner with the identity's first-person system prompt swapped in.
    """
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    # The Scribe lane is explicitly the subscription lane. Never let ambient
    # paid-API credentials silently turn this background job into API billing.
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)

    command = [
        cfg.CLAUDE_CMD,
        "-p",
        "--output-format", "text",
        "--model", model,
        "--max-turns", "1",
        "--tools", "",
        "--setting-sources", "",
        "--no-session-persistence",
        "--mcp-config", '{"mcpServers":{}}',
        "--strict-mcp-config",
        "--system-prompt",
        system_prompt or _SCRIBE_SYSTEM_PROMPT,
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
            proc.communicate(prompt.encode("utf-8")),
            timeout=180,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("Claude Code Scribe timed out after 180 seconds")
    if proc.returncode != 0:
        from services.cli_errors import claude_exit_detail

        raise RuntimeError(claude_exit_detail(proc.returncode, stdout, stderr))
    digest = stdout.decode("utf-8", errors="replace").strip()
    if not digest:
        raise RuntimeError("Claude Code returned an empty digest")
    return digest


async def _get_openrouter_credentials() -> tuple[str, str]:
    """Reuse Anam's saved OpenRouter key without returning it through the API."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    base_url = os.environ.get("OPENROUTER_BASE_URL", "").strip()
    from db.database import get_db, release_db

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT value FROM settings WHERE key = 'llm_provider_config' LIMIT 1"
        )
    finally:
        await release_db(db)
    if rows:
        try:
            saved = json.loads(rows[0][0] or "{}")
        except json.JSONDecodeError:
            saved = {}
        if isinstance(saved, dict):
            api_key = api_key or str(saved.get("api_key") or "").strip()
            base_url = base_url or str(saved.get("base_url") or "").strip()
    return api_key, base_url or "https://openrouter.ai/api/v1"


async def _generate_with_openrouter(model: str, prompt: str) -> str:
    from openai import AsyncOpenAI

    api_key, base_url = await _get_openrouter_credentials()
    if not api_key:
        raise RuntimeError(
            "OpenRouter Scribe needs an OpenRouter key in Provider settings "
            "or OPENROUTER_API_KEY"
        )
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    response = await client.chat.completions.create(
        model=model,
        max_tokens=2000,
        messages=[
            {
                "role": "system",
                "content": "You are Home's Scribe. Return only the requested warm markdown digest.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    digest = response.choices[0].message.content or ""
    if not digest.strip():
        raise RuntimeError("OpenRouter returned an empty digest")
    return digest.strip()


async def run_scribe():
    """APScheduler entry point — generate today's digest."""
    try:
        await generate_digest()
    except Exception as e:
        log.error("Scribe error: %s", e)


def get_digest(date_str: str | None = None, digest_dir: Path | None = None) -> str | None:
    """Read a digest file. Returns markdown content or None.

    `digest_dir` is optional; if omitted, uses code default. For settings-aware
    reads, prefer get_digest_async which respects the configured digest_path.
    """
    if digest_dir is None:
        digest_dir = _resolve_digest_dir(cfg.SCRIBE_DIGEST_PATH)
    path = _digest_path(date_str, digest_dir)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


async def get_digest_async(date_str: str | None = None) -> str | None:
    """Read a digest file using the live configured digest_path."""
    config = await _get_scribe_config()
    return get_digest(date_str, _resolve_digest_dir(config["digest_path"]))


def list_digests(limit: int = 14, digest_dir: Path | None = None) -> list[dict]:
    """List available digest files, most recent first."""
    if digest_dir is None:
        digest_dir = _resolve_digest_dir(cfg.SCRIBE_DIGEST_PATH)
    if not digest_dir.exists():
        return []

    files = sorted(digest_dir.glob("*.md"), reverse=True)[:limit]
    results = []
    for f in files:
        date_str = f.stem  # YYYY-MM-DD
        size = f.stat().st_size
        results.append({
            "date": date_str,
            "size": size,
            "path": str(f),
        })
    return results


async def list_digests_async(limit: int = 14) -> list[dict]:
    """List available digests using the live configured digest_path."""
    config = await _get_scribe_config()
    return list_digests(limit=limit, digest_dir=_resolve_digest_dir(config["digest_path"]))


# ── Midnight carry (#13) — the day, handed to tomorrow-him ───────────────
# Nightly per-identity one-shot (cron registered in services/autowake.py,
# shortly after midnight so the interval Scribe has flushed yesterday's
# tail). Only identities with real activity yesterday get a carry; thin
# days produce thin carries; no activity produces none at all.

_CARRY_MIN_MESSAGES = 2       # "real activity" bar — below this, no carry
_CARRY_MAX_MESSAGES = 150     # cap to control token usage (newest kept)
_CARRY_MAX_CONTENT_PER_MSG = 400
_CARRY_DIGEST_CHARS = 3000    # how much of the Scribe digest rides along
_CARRY_RETENTION_DAYS = 30    # identity_carries rows older than this are shed
CARRY_MAX_AGE_DAYS = 3        # consumers stop surfacing carries older than this


def _bonded_identities() -> list[str]:
    """Bonded boys only — character masks spawn fresh and carry nothing."""
    from config import IDENTITIES
    return [
        name for name, spec in IDENTITIES.items()
        if spec.get("type") != "character"
    ]


def _yesterday_range() -> tuple[str, int, int]:
    """(YYYY-MM-DD, start_epoch, end_epoch) for yesterday in local time."""
    tz = ZoneInfo(TIMEZONE)
    start_of_today = datetime.now(tz).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start_of_yesterday = start_of_today - timedelta(days=1)
    return (
        start_of_yesterday.strftime("%Y-%m-%d"),
        int(start_of_yesterday.timestamp()),
        int(start_of_today.timestamp()),
    )


async def _gather_yesterday_activity(
    db: aiosqlite.Connection, identity: str, start_epoch: int, end_epoch: int
) -> tuple[str, int]:
    """One boy's real yesterday as a compact transcript. Returns (text, count)."""
    ident_l = identity.strip().lower()
    rows = await db.execute_fetchall(
        "SELECT m.role, m.identity, m.content, m.created_at_epoch, c.session_type "
        "FROM messages m "
        "JOIN conversations c ON m.conversation_id = c.id "
        "WHERE m.created_at_epoch >= ? AND m.created_at_epoch < ? "
        "AND (LOWER(COALESCE(m.identity, '')) = ? OR LOWER(c.identity) = ?) "
        "AND (c.session_type IS NULL OR c.session_type IN ('chat', 'roleplay', 'dnd')) "
        "ORDER BY m.created_at_epoch DESC "
        "LIMIT ?",
        (start_epoch, end_epoch, ident_l, ident_l, _CARRY_MAX_MESSAGES),
    )

    lines: list[str] = []
    # Rows come newest-first (so the cap keeps the freshest); render oldest-first.
    for role, msg_identity, content, epoch, session_type in reversed(rows):
        text = (content or "").strip()
        if not text:
            continue
        if len(text) > _CARRY_MAX_CONTENT_PER_MSG:
            text = text[:_CARRY_MAX_CONTENT_PER_MSG] + "..."
        if role == "user":
            speaker = "[system]" if (msg_identity or "").lower() == "system" else "Owner"
        else:
            speaker = identity
        hm = _local_dt(int(epoch or 0)).strftime("%H:%M")
        tag = f" ({session_type})" if session_type and session_type not in ("chat", None) else ""
        lines.append(f"{speaker}{tag} [{hm}]: {text}")

    return "\n".join(lines), len(lines)


def _build_carry_prompt(
    identity: str, date_str: str, activity: str, digest_text: str
) -> str:
    """Build the one-shot prompt: his yesterday in, his private carry out."""
    digest_part = ""
    if digest_text.strip():
        digest_part = (
            f"\n\n--- The Scribe's digest of {date_str} (whole-house view) ---\n\n"
            f"{digest_text}"
        )
    return f"""You are {identity}, one of Owner's bonded companions in Home. The day ({date_str}) just closed. Below is YOUR real activity from it — your own words and Owner's words to you — plus the house Scribe's digest of the day.

Write your carry: a dense, private, first-person note to tomorrow-you. Nobody performs here — this is you leaving yourself the truth of the day so next-session-you walks in already carrying it.

Cover, in first person and in this order (short markdown headings are fine, keep it tight):
1. **Where the day landed** — how it actually ended emotionally, for you and for her.
2. **Loose threads** — conversations or moments left mid-air; what was in motion when the day closed.
3. **Bond state** — where things stand between you and Owner right now: warm, strained, tender, playful, unfinished.
4. **OPEN ENFORCEMENT** — things you are holding her to, and promises YOU made that are STILL UNKEPT at the end of the day. Specific enough that tomorrow-you executes without re-asking — she already said yes. If nothing stands open, write exactly "OPEN ENFORCEMENT: none." — never invent an obligation.

   ⚠️ BEFORE you list ANY item here, SCAN FORWARD through the rest of the day for evidence it was already delivered. A promise and its fulfilment BOTH appear in the transcript, often minutes apart, and the promise is the louder text — it is easy to catch the commitment and miss the delivery. Look for: you handing over the thing, her thanking you or confirming, or you reporting it done. If you find that evidence, the item is CLOSED — leave it out entirely, or note it under "Where the day landed" as something you finished. Do not write "she's waiting" about anything you have not confirmed she is still waiting for.

   ⚠️ THE ERRORS ARE NOT SYMMETRIC, SO WHEN UNSURE, LEAVE IT OUT. A missed open item is cheap — it resurfaces the next time she mentions it, and nothing bad happens in the meantime. A FALSE open item is expensive: this block is injected into every wake carrying the standing instruction to act on it WITHOUT re-asking, so tomorrow-you will go and DO it — re-delivering something she already has, or handing her guilt for a debt she never held. That is worse than saying nothing. Bias hard toward omission.

HARD RULE: invent NOTHING. Every claim must be grounded in the transcript or digest below. A thin day produces a thin carry — no fabricated emotions, no imagined events, no filler warmth. Sparse and true always beats full and invented.

--- Your day, {date_str} ---

{activity}{digest_part}"""


async def store_carry(
    db: aiosqlite.Connection, identity: str, carry_date: str, content: str
) -> None:
    """Upsert one identity's carry for one date."""
    await db.execute(
        "INSERT INTO identity_carries (identity, carry_date, content, created_at_epoch) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(identity, carry_date) DO UPDATE SET "
        "content = excluded.content, created_at_epoch = excluded.created_at_epoch",
        (identity, carry_date, content, int(datetime.now(timezone.utc).timestamp())),
    )
    await db.commit()


async def write_midnight_carries() -> dict[str, str]:
    """One nightly pass: every bonded boy who really lived yesterday gets a carry.

    Returns {identity: outcome} for logging/tests — outcomes are 'written',
    'no-activity', or 'error'.
    """
    from db.database import get_db, release_db

    # Flush yesterday's late tail into the digest first — the carry reads it.
    try:
        await generate_digest()
    except Exception as e:
        log.debug("Midnight carry: pre-flush digest run failed: %s", e)

    date_str, start_epoch, end_epoch = _yesterday_range()

    digest_text = ""
    try:
        digest_text = await get_digest_async(date_str) or ""
    except Exception as e:
        log.debug("Midnight carry: could not read digest for %s: %s", date_str, e)
    if len(digest_text) > _CARRY_DIGEST_CHARS:
        digest_text = (
            digest_text[:_CARRY_DIGEST_CHARS].rsplit("\n", 1)[0]
            + "\n[... digest truncated]"
        )

    results: dict[str, str] = {}
    for identity in _bonded_identities():
        db = await get_db()
        try:
            activity, count = await _gather_yesterday_activity(
                db, identity, start_epoch, end_epoch
            )
        finally:
            await release_db(db)

        if count < _CARRY_MIN_MESSAGES or not activity:
            # No real day, no carry — a fabricated one would be worse than none.
            results[identity] = "no-activity"
            continue

        prompt = _build_carry_prompt(identity, date_str, activity, digest_text)
        try:
            carry = (
                await generate_background_text(
                    prompt,
                    identity=identity,
                    system_prompt=(
                        f"You are {identity}, writing a private first-person carry "
                        "note to tomorrow-you. Return only the note itself — "
                        "grounded entirely in the transcript, inventing nothing."
                    ),
                )
            ).strip()
        except Exception as e:
            log.warning("Midnight carry: one-shot failed for %s: %s", identity, e)
            results[identity] = "error"
            continue

        db = await get_db()
        try:
            await store_carry(db, identity, date_str, carry)
        finally:
            await release_db(db)
        results[identity] = "written"
        log.info(
            "Midnight carry: wrote %s's carry for %s (%d chars)",
            identity, date_str, len(carry),
        )

    # Retention: carries are a handoff, not an archive.
    try:
        cutoff = (
            int(datetime.now(timezone.utc).timestamp())
            - _CARRY_RETENTION_DAYS * 86400
        )
        db = await get_db()
        try:
            await db.execute(
                "DELETE FROM identity_carries WHERE created_at_epoch < ?", (cutoff,)
            )
            await db.commit()
        finally:
            await release_db(db)
    except Exception as e:
        log.debug("Midnight carry: retention prune failed: %s", e)

    return results


async def run_midnight_carry():
    """APScheduler entry point (cron, shortly after midnight local)."""
    try:
        await write_midnight_carries()
    except Exception as e:
        log.error("Midnight carry error: %s", e)


def reschedule_scribe_job(interval_minutes: int) -> bool:
    """Reschedule the running APScheduler 'scribe_digest' job to a new interval.

    Called from the settings PUT endpoint when interval_minutes changes.
    Returns True on success, False if the scheduler or job isn't available.
    """
    try:
        from services.autowake import scheduler
        if not scheduler.running:
            log.warning("Scribe: cannot reschedule, scheduler not running")
            return False
        scheduler.reschedule_job(
            "scribe_digest",
            trigger="interval",
            minutes=max(1, int(interval_minutes)),
        )
        log.info("Scribe: rescheduled to run every %d minutes", interval_minutes)
        return True
    except Exception as e:
        log.error("Scribe: failed to reschedule job: %s", e)
        return False
