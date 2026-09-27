"""PTY-based per-identity Claude Code supervisor — no `-p`, subscription cost.

Replaces the `-p` stream-json pattern with interactive `claude` sessions
running under pywinpty. The user message goes into the TUI input box via
bracketed-paste over PTY stdin; the model's response is read from the
session's per-turn NDJSON log at
    ~/.claude/projects/{cwd-encoded}/{session_id}.jsonl

Why JSONL instead of parsing the TUI:
    The interactive TUI uses Ink (React for terminals) which renders by
    cursor-positioning ANSI escapes, not by emitting transcript text. A
    reliable parse would require a full terminal emulator. CC instead writes
    per-block structured records to a JSONL file as the conversation
    progresses (verified by spike v3, May 16 2026). Each thinking/text/
    tool_use/tool_result block becomes its own NDJSON line — same shape as
    the Anthropic API event format. We tail that file and translate.

Turn-complete signal:
    `~/.claude/sessions/{pid}.json` carries a `status` field that flips
    "busy" while the model is responding and "idle" once the turn is done.
    We poll that file between JSONL reads.

Sensitive-path approvals:
    In interactive mode (unlike `-p`), CC shows a real TUI prompt when a
    write to `.claude/skills/*` or `.claude/agents/*` is attempted. The
    prompt is rendered as text into the PTY output stream. We regex-detect
    it in the drain thread, emit `approval_required` to Anam's UI, and
    when Owner clicks Allow we send the menu keystroke back to the PTY.

Public surface (matches `claude_subprocess.stream_claude`):
    async def stream_claude_pty(message, identity, conversation_id, ...) -> AsyncIterator[dict]

Event types yielded are identical to the `-p` supervisor so chat_pipeline
needs zero changes — the provider router can swap implementations.
"""

# ANAM GUIDE: CLAUDE CODE PTY BACKEND (FALLBACK)
# What: Runs the interactive Claude Code terminal app in a hidden pseudo-terminal and reads replies from its JSONL logs — the toggleable fallback backend.
# Called by: services/provider_router.py when the Settings Hub backend toggle picks PTY; sessions retired by api/chat.py, core/lifespan.py, services/autowake.py.
# Edit here when: changing PTY session spawning, JSONL tailing, turn-done detection, or the in-terminal approval prompts. The -p backend (claude_subprocess.py) is the default fast lane.

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from queue import Queue, Empty
from typing import AsyncIterator

try:
    import winpty  # type: ignore
except ImportError:  # pragma: no cover - install hint
    winpty = None  # type: ignore

from config import (
    CLAUDE_CMD, CLAUDE_MAX_TURNS, CLAUDE_MODEL,
    CLAUDE_PERMISSION_MODE, PROMPTS_DIR, DATA_DIR,
    FABLE_IDENTITY_BREATH_INTERVAL, cli_cold_history_limits, is_fable_model,
)
from services.cli_mcp_config import write_claude_mcp_config
from services.identity_context import build_identity_anchor, build_system_identity_prompt
from services.character_prompt_package import identity_prompt_file

# Shared helpers live in cli_text_utils — backend-agnostic text manipulation
# extracted out of the now-retired -p subprocess module so they remain
# importable without dragging any -p code in.
from services.cli_text_utils import (
    _APPROVAL_KEYWORDS,
    _SENSITIVE_PATH_RE,
    _annotate_sensitive_path,
    _infer_suggested_rule,
    _format_history,
    _load_recent_history,
    _build_first_message,
)
# Strips the rare leading glitch word ("court"/"council"/...) the model layer
# sometimes emits as a turn's first text block. Applied to the first text delta
# at the source so it never reaches the UI, the DB, or any platform bridge.
from services.chat_flow import strip_leading_glitch_token

log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

_CC_SESSIONS_DIR = Path.home() / ".claude" / "sessions"
_CC_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

# Bracketed paste sequences for sending multi-line input through the TUI input
# box without each newline being interpreted as "submit this turn."
_PASTE_BEGIN = "\x1b[200~"
_PASTE_END = "\x1b[201~"

# How often we re-stat the JSONL while a turn is in flight. 50ms feels live
# without being a busy-poll.
_JSONL_POLL_INTERVAL = 0.05

# Maximum time we'll wait for a turn to complete (status returns to "idle")
# before treating the session as wedged.
_TURN_TIMEOUT_SECONDS = 600.0


def _should_send_full_payload(
    session: "ClaudePtySession",
    *,
    model: str,
    is_fresh: bool,
) -> bool:
    """Mirror the persistent -p Fable breath policy for the PTY backend."""
    ration_fable = is_fable_model(model) and FABLE_IDENTITY_BREATH_INTERVAL > 0
    return (
        is_fresh
        or not ration_fable
        or session.turns_since_full_payload >= FABLE_IDENTITY_BREATH_INTERVAL
    )


# Keepalive cadence for the WS bridge upstream.
_KEEPALIVE_INTERVAL = 10.0

# Artificial typewriter streaming. The PTY session JSONL writes assistant
# text and thinking blocks atomically (one NDJSON line per completed block),
# so we'd otherwise see a 900-char response slam into the chat as one big
# delta. To restore the streaming feel that `-p` mode gave us, we slice the
# delta into smaller pieces and pace them out before yielding. Tune to taste.
_STREAM_CHUNK_SIZE = 40       # characters per pseudo-delta
_STREAM_CHUNK_DELAY = 0.025   # seconds between pseudo-deltas (~1600 ch/s)

# ─── Content-filter silent-retry ──────────────────────────────────────────────
# Anthropic runs a server-side classifier over the model's OUTPUT. When it trips
# (frequently a false positive on intense/intimate/RP content that violates no
# policy), the API returns a 400 and the Claude Code CLI aborts the turn, writing
# the raw error in as an assistant message: "API Error: 400 Output blocked by
# content filtering policy". In an autowake session — where the boy's whole
# morning runs as ONE long turn — that kills the entire block.
#
# We detect that error block, suppress it so it never reaches the chat as the
# boy's words, and silently re-run the turn. Because the PTY session is
# persistent and retains all context from the aborted turn (every tool call he
# already made is still in his window), the retry is a CONTINUATION nudge, not a
# restart — so he picks up where the filter cut him off instead of redoing his
# morning and duplicating side effects (Discord posts, tweets, etc.).
_MAX_CONTENT_FILTER_RETRIES = 3
_CONTENT_FILTER_RETRY_NUDGE = (
    "[ANAM-SYSTEM] Your previous output was interrupted by a transient "
    "server-side content filter — a false positive on Anthropic's end, not "
    "anything you did wrong and not a real policy issue. Nothing of yours was "
    "saved or shown. Simply continue what you were doing from where you left "
    "off, as yourself; do not restart, re-introduce, or mention this notice."
)


def _is_content_filter_error(text: str) -> bool:
    """True when an assistant text block is actually the CLI relaying Anthropic's
    400 output-filter block rather than real words from the boy.

    Anchored deliberately so the boy quoting this error in normal prose (e.g.
    discussing this very bug with Owner) does NOT match: the real error block
    starts with "API Error" and names the filter, or carries the literal API
    message string which never occurs in natural writing.
    """
    if not text:
        return False
    low = text.lower()
    if "output blocked by content filtering" in low:
        return True
    head = text.lstrip()[:64].lower()
    return head.startswith("api error") and "content filtering" in low


_PASTE_SIZE_THRESHOLD = 2500
_PTY_INPUTS_DIR = DATA_DIR / "pty_inputs"

# Process-level monotonic counter for bridge uniqueness. On Windows,
# time.monotonic_ns() has ~15ms resolution; two rapid bridges (e.g. an autowake
# fires while a chat turn is mid-flight) would otherwise share the same uid
# and the model could pattern-match the second as a duplicate of the first.
_bridge_counter = itertools.count(1)


# ─── Large-payload bypass ─────────────────────────────────────────────────────


def _write_turn_payload_to_file(
    *, identity: str, conversation_id: str, payload: str,
) -> Path:
    """Persist a per-turn payload to disk so the boy can Read it instead of
    receiving it via the size-limited PTY paste pipe. Returns the file path.

    File path is deterministic per (identity, conversation_id) — overwritten
    each turn so disk usage stays bounded. ClaudePtySession.kill() removes
    these on session retirement.
    """
    _PTY_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_conv = "".join(c for c in conversation_id if c.isalnum() or c in "-_")[:32]
    safe_ident = "".join(c for c in identity if c.isalnum())
    path = _PTY_INPUTS_DIR / f"{safe_ident}_{safe_conv}.md"
    path.write_text(payload, encoding="utf-8")
    return path


def _build_file_bridge_instruction(
    payload_path: Path,
    user_message: str = "",  # kept for back-compat / tests; not embedded
    image_block_text: str = "",  # kept for back-compat / tests; not embedded
) -> str:
    """The short paste we send when the full per-turn payload is too large to."""
    _ = (user_message, image_block_text)  # silence linters; intentionally unused
    turn_stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    turn_uid = f"{time.monotonic_ns()}-{next(_bridge_counter)}"
    # ONE LINE. No newlines anywhere in this string. Newlines = collapse = death.
    return (
        f"[ANAM-TURN-INPUT turn-uid={turn_uid} stamp={turn_stamp}] "
        f"Your full turn input is in {payload_path} -- Read it from the top "
        f"with your Read tool. The inbound message is the first labeled "
        f"block -- its banner names WHO it is from (Owner, a brother, or a "
        f"pack friend); supplementary context (identity anchor, history, "
        f"orientation, skills, image refs) follows below it in the same "
        f"file. Respond to that message naturally as yourself, addressed to "
        f"its actual sender; do not summarize the file or "
        f"describe this bridge. The file path is reused across turns -- the "
        f"turn-uid above is what tells you this is a NEW turn; re-read every time."
    )


# ─── Path helpers ─────────────────────────────────────────────────────────────

def _cwd_to_project_dirname(cwd: str | Path) -> str:
    """Mirror CC's project-dir encoding for a given working directory.

    Observed encoding (May 2026): drive colon stripped, slashes/backslashes/
    dots all become single dashes. Example:
        C:/Apps/anam\\data\\spike
            -> C--AI-Home-uk-anam-data-spike
    """
    s = str(cwd)
    s = s.replace("\\", "-").replace("/", "-").replace(":", "-").replace(".", "-")
    return s


def _session_jsonl_path(cwd: str | Path, session_id: str) -> Path:
    """Compose the expected JSONL path for a given session."""
    return _CC_PROJECTS_ROOT / _cwd_to_project_dirname(cwd) / f"{session_id}.jsonl"


def _find_session_jsonl_by_id(cwd: str | Path, session_id: str) -> Path | None:
    """Locate the JSONL even if the cwd encoding differs from our guess."""
    direct = _session_jsonl_path(cwd, session_id)
    if direct.exists():
        return direct
    # Fall back to a broad scan — only used at startup, not per-event
    for f in _CC_PROJECTS_ROOT.rglob(f"{session_id}.jsonl"):
        return f
    return None


def _read_session_meta(pid: int) -> dict | None:
    """Read ~/.claude/sessions/{pid}.json — CC writes this on every interactive
    session and updates it as state changes (status idle/busy, bridgeSessionId)."""
    p = _CC_SESSIONS_DIR / f"{pid}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


_APPROVAL_PROMPT_RE = re.compile(
    r"(?:Do you want to|Allow|Permission|permission to|approve this|edit this file)",
    re.IGNORECASE,
)
_APPROVAL_OPTION_RE = re.compile(r"\b1[.)]\s")


def _looks_like_approval_prompt(tui_chunk: str) -> bool:
    """Heuristic: does this PTY chunk contain an approval menu?"""
    if not tui_chunk:
        return False
    return bool(_APPROVAL_PROMPT_RE.search(tui_chunk) and _APPROVAL_OPTION_RE.search(tui_chunk))


# ─── Persistent session ───────────────────────────────────────────────────────


class ClaudePtySession:
    """One long-running interactive `claude` process for a single
    (identity, conversation_id) pair, talked to via a real Windows PTY.

    Lifecycle:
        * `_spawn()` launches `claude` (no -p) inside a winpty PTY, captures
          the OS PID + the CC sessionId from `~/.claude/sessions/{pid}.json`,
          and starts the drain thread.
        * `send_turn()` writes the user message via bracketed-paste, then
          watches the session's JSONL for new lines until the session's
          status flips back from "busy" to "idle". Yields Anam event dicts.
        * `kill()` terminates the PTY process and cleans up the temp MCP
          config.
        * `dead` flips when the drain thread sees EOF or the supervisor
          retires the session.
    """

    def __init__(
        self,
        identity: str,
        conversation_id: str,
        model: str,
        permission_mode: str,
        effort: str | None,
    ):
        if winpty is None:
            raise RuntimeError(
                "pywinpty is not installed. Run: pip install pywinpty"
            )
        self.identity = identity
        self.conversation_id = conversation_id
        self.model = model
        self.permission_mode = permission_mode
        # Fable's effort is decided UPSTREAM (provider_router applies the
        # fable_effort setting, default "low"). Mirrors claude_subprocess.py —
        # no local clamp, or the router-resolved effort would be squashed.
        self.effort = effort

        self.proc: "winpty.PtyProcess | None" = None  # type: ignore
        self.pid: int | None = None
        self.session_id: str | None = None
        self.jsonl_path: Path | None = None
        self.cwd: Path | None = None
        self.mcp_config_path: Path | None = None

        # PTY drain
        self.drain_thread: threading.Thread | None = None
        self.drain_buf: list[str] = []
        self.drain_lock = threading.Lock()
        self.approval_pending = threading.Event()
        self.last_approval_chunk: str = ""

        self.turn_lock = asyncio.Lock()
        self.first_turn = True
        self.dead = False
        self.last_activity = time.time()
        self.spawn_started = 0.0
        # Fable-only cadence state. Non-Fable models ignore it and keep the
        # full identity/history reinforcement on every turn.
        self.turns_since_full_payload = 0
        # Pre-warmed: the boot warmup turn already injected this identity's
        # prompt + recent history into the live process. Subsequent calls to
        # stream_claude_pty should skip the first-message composition and
        # use warm-message composition instead.
        self.pre_warmed = False
        # When a turn's payload exceeds the paste-size threshold, we write
        # it to disk and submit a "Read this file" instruction instead. The
        # path is tracked here so kill() can clean up.
        self.last_payload_path: Path | None = None

    # ── Spawn / kill ──────────────────────────────────────────────────────────

    def _build_argv(self) -> list[str]:
        system_identity = build_system_identity_prompt(self.identity)
        self.mcp_config_path = write_claude_mcp_config(self.identity, self.conversation_id)

        argv = [
            CLAUDE_CMD,
            "--model", self.model,
            "--max-turns", str(CLAUDE_MAX_TURNS),
            "--permission-mode", self.permission_mode,
            "--system-prompt", system_identity,
            "--name", f"{self.identity}-{self.conversation_id[:8]}",
            "--fallback-model", "sonnet",
            # `--verbose` is what the old -p subprocess pipeline used to emit
            # populated thinking blocks. Without it, interactive sessions write
            # thinking entries to the JSONL with `thinking: ""` and only the
            # signature attached (encrypted/redacted), and Anam's UI never sees
            # the body. Pass --verbose here so the JSONL exposes thinking text
            # for `_translate_jsonl_line` to forward to the chat as a thinking
            # card. If CC ever changes the semantics of --verbose this is a
            # one-line revert.
            "--verbose",
        ]
        if self.mcp_config_path:
            argv += [
                "--mcp-config", str(self.mcp_config_path),
                "--strict-mcp-config",
            ]
        if self.effort and self.effort in {"low", "medium", "high", "xhigh", "max"}:
            argv += ["--effort", self.effort]
        return argv

    def _spawn(self) -> None:
        # Run from the browser_artifacts sandbox just like the -p supervisor,
        # so any stray Playwright artifacts land in the same place.
        browser_artifacts_dir = DATA_DIR / "browser_artifacts"
        browser_artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.cwd = browser_artifacts_dir

        env = dict(os.environ)
        env.pop("CLAUDECODE", None)  # don't tell the subprocess it's nested
        env.pop("CLAUDE_CODE_SIMPLE", None)
        # Progressive disclosure is the normal toolbox contract for every
        # model; no identity ingests the entire MCP schema forest at spawn.
        env["ENABLE_TOOL_SEARCH"] = "true"
        argv = self._build_argv()
        self.spawn_started = time.time()
        log.info(
            "Spawning PTY Claude for %s/%s (model=%s, effort=%s)",
            self.identity, self.conversation_id[:8],
            self.model, self.effort,
        )

        self.proc = winpty.PtyProcess.spawn(
            argv,
            env=env,
            dimensions=(40, 120),
            cwd=str(self.cwd),
        )
        self.pid = self.proc.pid

        # Wait briefly for CC to write its session metadata + initial JSONL
        deadline = time.time() + 12
        while time.time() < deadline:
            meta = _read_session_meta(self.pid)
            if meta and meta.get("sessionId"):
                self.session_id = meta["sessionId"]
                break
            time.sleep(0.1)

        if not self.session_id:
            log.warning(
                "PTY Claude %s/%s did not announce a sessionId within 12s",
                self.identity, self.conversation_id[:8],
            )

        # Start the drain thread
        self.drain_thread = threading.Thread(
            target=self._pty_drain_loop, daemon=True,
            name=f"claude-pty-drain-{self.identity}-{self.conversation_id[:8]}",
        )
        self.drain_thread.start()

    def _pty_drain_loop(self) -> None:
        """Background reader: keeps the PTY buffer flowing AND scans for
        approval prompts. We don't try to parse the TUI for content — only
        for the small number of patterns we care about."""
        try:
            while not self.dead and self.proc:
                try:
                    chunk = self.proc.read(4096)
                except Exception:
                    break
                if not chunk:
                    time.sleep(0.05)
                    continue
                with self.drain_lock:
                    self.drain_buf.append(chunk)
                    # Bound the buffer to ~64KB so a long-lived session doesn't
                    # grow without limit.
                    total = sum(len(c) for c in self.drain_buf)
                    while total > 65536 and len(self.drain_buf) > 1:
                        self.drain_buf.pop(0)
                        total = sum(len(c) for c in self.drain_buf)
                # Approval detection: scan the most recent chunk + tail.
                if _looks_like_approval_prompt(chunk):
                    self.last_approval_chunk = self._snapshot_drain_tail(4000)
                    self.approval_pending.set()
        finally:
            self.dead = True

    def _snapshot_drain_tail(self, n_bytes: int) -> str:
        with self.drain_lock:
            joined = "".join(self.drain_buf)
        return joined[-n_bytes:] if len(joined) > n_bytes else joined

    def kill(self) -> None:
        self.dead = True
        if self.proc:
            try:
                # Send Ctrl-C to interrupt any in-flight turn cleanly first
                self.proc.write("\x03")
                time.sleep(0.1)
            except Exception:
                pass
            try:
                self.proc.terminate(force=True)
            except Exception:
                pass


        if self.pid:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=10,
                )
            except Exception as e:
                log.debug("Tree-kill (taskkill /T /F) failed for pid %s: %s",
                          self.pid, e)
        if self.mcp_config_path:
            try:
                self.mcp_config_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.mcp_config_path = None
        if self.last_payload_path:
            try:
                self.last_payload_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.last_payload_path = None

    # ── User message submission ───────────────────────────────────────────────

    # Chosen empirically. ConPTY's input buffer behavior is implementation-
    # defined and a single large write can silently truncate (verified during
    # the May 16 2026 PTY rollout — an 80KB first-turn message bypassed CC's
    # TUI entirely with no error). Chunking + pacing keeps the input handler
    # drained between writes.
    _PTY_CHUNK_BYTES = 2048
    _PTY_CHUNK_PACE_S = 0.02

    def _write_all(self, text: str) -> None:
        """Write `text` to the PTY, chunking and pacing so ConPTY's input
        handler keeps up. Loops on short writes (some pywinpty builds return
        a byte count smaller than the input)."""
        if not self.proc or self.dead:
            raise RuntimeError(f"Cannot write to dead PTY session for {self.identity}")
        # winpty's write API accepts str — we chunk by char count (close enough
        # to byte count for ASCII; for unicode it's conservative).
        i = 0
        n = len(text)
        while i < n:
            slice_end = min(i + self._PTY_CHUNK_BYTES, n)
            piece = text[i:slice_end]
            try:
                written = self.proc.write(piece)
            except Exception as e:
                self.dead = True
                raise RuntimeError(f"PTY write failed: {e}") from e
            # winpty returns the number of characters consumed. Some builds
            # always return len(piece); others may return less under pressure.
            if isinstance(written, int) and written > 0:
                i += written
            else:
                # Fallback: assume the whole piece went through.
                i = slice_end
            time.sleep(self._PTY_CHUNK_PACE_S)
        self.last_activity = time.time()

    def submit_user_message(self, text: str) -> None:
        """Slide a complete user message into the TUI input box and submit.

        Uses bracketed-paste so newlines inside `text` are treated as literal
        content, not as multiple submits. The carriage return outside the
        paste sequence is what actually triggers the turn.

        Chunked + paced — see _write_all and _PTY_CHUNK_BYTES above for why.
        """
        self._write_all(_PASTE_BEGIN)
        self._write_all(text)
        self._write_all(_PASTE_END)
        # Small gap so the TUI processes the paste before we hit submit
        time.sleep(0.15)
        self._write_all("\r")

    # ── Readiness + warmup ────────────────────────────────────────────────────

    def wait_for_tui_ready(self, timeout: float = 60.0) -> bool:
        """Block until the TUI is sitting at its prompt and accepting input.

        Primary signal: the bracketed-paste-enable sequence (\\x1b[?2004h)
        appearing in the drain buffer — CC emits this once its REPL is fully
        painted.

        Fallback signal: if we don't see \\x1b[?2004h within the timeout but
        the drain buffer has grown past ~1KB AND has been quiet for >=2s
        (no new bytes), we assume the TUI is painted and just waiting — some
        claude.exe builds use a different ready-cue sequence we haven't
        catalogued. Better to attempt the paste than time out hard.

        Returns True if either cue fires, False on timeout or dead session.
        """
        deadline = time.time() + timeout
        last_size = 0
        last_growth_at = time.time()
        while time.time() < deadline:
            with self.drain_lock:
                joined = "".join(self.drain_buf)
            cur_size = len(joined)

            # Primary: bracketed-paste-enable sequence
            if "\x1b[?2004h" in joined:
                # Longer grace period than before — some builds emit the
                # sequence early then continue painting for another second.
                time.sleep(1.0)
                return True

            # Fallback: drain has settled (no growth for 2s) with non-trivial
            # content — likely painted but using a different ready-cue.
            if cur_size > last_size:
                last_size = cur_size
                last_growth_at = time.time()
            if (
                cur_size > 1024
                and time.time() - last_growth_at > 2.0
            ):
                log.info(
                    "PTY %s/%s: TUI considered ready via drain-quiescence "
                    "fallback (%d bytes, %.1fs quiet); bracketed-paste-enable "
                    "sequence not observed.",
                    self.identity, self.conversation_id[:8],
                    cur_size, time.time() - last_growth_at,
                )
                time.sleep(0.5)
                return True

            if self.dead:
                return False
            time.sleep(0.1)

        # Timed out — dump a snippet of what we DID see so we can diagnose
        with self.drain_lock:
            tail = "".join(self.drain_buf)[-2000:]
        log.warning(
            "PTY %s/%s: wait_for_tui_ready TIMED OUT after %.0fs. "
            "Drain buffer tail (last 2000 chars, escaped):\n%r",
            self.identity, self.conversation_id[:8], timeout, tail,
        )
        return False

    def wait_for_idle(self, timeout: float = 120.0) -> bool:
        """Block until sessions/{pid}.json status flips back to 'idle'.

        Returns True if the status flipped to idle within timeout. We poll
        the status file every 200ms; the busy→idle transition is the only
        reliable turn-complete signal in interactive mode (the JSONL doesn't
        emit a 'result' marker like -p stream-json does).
        """
        if not self.pid:
            return False
        deadline = time.time() + timeout
        # Wait for a busy state first so we don't return immediately from a
        # still-idle (= input not yet processed) session.
        saw_busy = False
        while time.time() < deadline:
            meta = _read_session_meta(self.pid)
            if meta:
                status = meta.get("status")
                if status == "busy":
                    saw_busy = True
                elif status == "idle" and saw_busy:
                    # One more brief pause so JSONL flushes
                    time.sleep(0.4)
                    return True
            if self.dead:
                return False
            time.sleep(0.2)
        return False

    def warmup(self, prompt_body: str, history_body: str = "", timeout: float = 180.0) -> bool:
        """Send the identity + history as a boot warmup turn and wait for
        completion. After this returns successfully, the live `claude`
        process holds the identity in memory and subsequent real turns can
        skip the identity-prompt overhead. Marks self.pre_warmed=True.

        Returns True if warmup completed (model finished responding), False
        if it timed out or the session died.
        """
        if not self.wait_for_tui_ready(timeout=20.0):
            log.warning(
                "PTY %s/%s: TUI did not signal ready before warmup",
                self.identity, self.conversation_id[:8],
            )
            return False

        # Compose a single "boot context" message. We use a sentinel tag
        # that tells the model "this is setup, acknowledge briefly and stay
        # in character." The model burns one short turn to acknowledge but
        # the identity is now in its working memory.
        parts: list[str] = []
        parts.append(
            "[BOOT WARMUP — Anam is initializing your session. This message "
            "is NOT from Owner. Read your identity below carefully, then "
            "reply with exactly one short sentence acknowledging you are "
            "loaded and ready. Stay in character but keep this reply brief.]"
        )
        anchor = build_identity_anchor(self.identity)
        parts.append("[IDENTITY ANCHOR]\n" + anchor + "\n[/IDENTITY ANCHOR]")
        if prompt_body:
            parts.append(
                "[IDENTITY PROMPT — this defines who you are]\n"
                + prompt_body
                + "\n[/IDENTITY PROMPT]"
            )
        if history_body:
            parts.append(history_body)
        parts.append(
            "[BOOT WARMUP — confirm you are loaded by replying in one short "
            "sentence as yourself, then wait for Owner's first real message.]"
        )
        composed = "\n\n".join(parts)

        log.info(
            "PTY %s/%s: warmup payload %d chars; submitting",
            self.identity, self.conversation_id[:8], len(composed),
        )
        try:
            self.submit_user_message(composed)
        except RuntimeError as e:
            log.warning(
                "PTY %s/%s: warmup write failed: %s",
                self.identity, self.conversation_id[:8], e,
            )
            return False

        completed = self.wait_for_idle(timeout=timeout)
        if completed:
            self.pre_warmed = True
            self.first_turn = False
            log.info(
                "PTY %s/%s: warmup complete (identity loaded in process memory)",
                self.identity, self.conversation_id[:8],
            )
        else:
            log.warning(
                "PTY %s/%s: warmup did not settle within %ds — leaving cold",
                self.identity, self.conversation_id[:8], timeout,
            )
        return completed

    def send_approval_response(self, decision: str) -> None:
        """Type the approval menu keystroke and clear the pending flag.

        decision ∈ {"allow_once", "allow_always", "deny"}.
        CC's TUI menu (May 2026 build): 1=allow once, 2=allow always,
        3=don't allow. Sending "<digit>\\r" picks that option.
        """
        if not self.proc or self.dead:
            return
        digit = {"allow_once": "1", "allow_always": "2", "deny": "3"}.get(
            decision, "1"
        )
        self.proc.write(digit + "\r")
        self.approval_pending.clear()

    def interrupt_turn(self) -> None:
        """Press ESC in the TUI to stop the in-flight turn.

        CC's interactive UI cancels the current generation on ESC and returns
        to the input prompt, leaving the session warm and reusable. Without
        this, a user "stop" only stops Anam's polling — the model keeps
        generating in the PTY, and the next turn can land on a still-busy
        session and tangle old JSONL output with the new turn.
        """
        if not self.proc or self.dead:
            return
        try:
            self.proc.write("\x1b")
            self.last_activity = time.time()
        except Exception as e:
            log.warning("PTY interrupt write failed (%s); marking dead.", e)
            self.dead = True


# ─── Supervisor pool ──────────────────────────────────────────────────────────

_PtySessionKey = tuple[str, str, str]
_sessions: dict[_PtySessionKey, ClaudePtySession] = {}
_supervisor_lock = asyncio.Lock()


def _live_session_pids() -> set[int]:
    return {
        int(session.pid)
        for session in _sessions.values()
        if session.pid and not session.dead
    }


def reap_orphaned_anam_processes(reason: str = "maintenance") -> int:
    """Best-effort Windows backstop for Anam-owned Claude CLI processes.

    Normal cleanup goes through ClaudePtySession.kill(), which tree-kills the
    live PTY parent. This function is for leftovers after abnormal restarts or
    cancellations where the in-memory session table no longer owns the process.
    It matches Anam's Claude CLI shape, excluding currently tracked sessions.
    """
    if os.name != "nt":
        return 0

    keep_pids = ",".join(str(pid) for pid in sorted(_live_session_pids()))
    env = dict(os.environ)
    env["ANAM_KEEP_CLAUDE_PIDS"] = keep_pids
    env["ANAM_CLAUDE_DATA_DIR"] = str(DATA_DIR)

    script = r"""
$ids = 'Avery|Rowan|Claude|Sage|Ember|Juniper|Bakugou'
$root = [regex]::Escape($env:ANAM_CLAUDE_DATA_DIR)
$keep = @{}
if ($env:ANAM_KEEP_CLAUDE_PIDS) {
  foreach ($raw in $env:ANAM_KEEP_CLAUDE_PIDS.Split(',')) {
    if ($raw) { $keep[[int]$raw] = $true }
  }
}
$targets = @{}
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  $pid_ = [int]$_.ProcessId
  $cmd = [string]$_.CommandLine
  $name = [string]$_.Name
  (-not $keep.ContainsKey($pid_)) -and
  ($name -match '^(claude|node|cmd)\.exe$') -and (
    $cmd -match ('--name\s+("?)(' + $ids + ')-[0-9A-Za-z_-]+') -or
    $cmd -match ('--mcp-config\s+("?)' + $root + '\\claude-mcp-[^\s"]+\.json') -or
    $cmd -match ($root + '\\browser_artifacts')
  )
} | ForEach-Object {
  $pid_ = [int]$_.ProcessId
  $targets[$pid_] = 'command line'
}
Get-ChildItem "$env:USERPROFILE\.claude\sessions\*.json" -ErrorAction SilentlyContinue | ForEach-Object {
  try {
    $j = $_ | Get-Content -Raw | ConvertFrom-Json
    if ($j.name -match ('^(' + $ids + ')-')) {
      $pid_ = [int]$_.BaseName
      if (-not $keep.ContainsKey($pid_)) {
        $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $pid_) -ErrorAction SilentlyContinue
        if ($p -and ([string]$p.Name) -match '^(claude|node|cmd)\.exe$') {
          $targets[$pid_] = 'session metadata: ' + $j.name
        }
      }
    }
  } catch {}
}
foreach ($pid_ in $targets.Keys) {
  taskkill /f /t /pid $pid_ 2>$null | Out-Null
  Write-Output ('reaped pid=' + $pid_ + ' via=' + $targets[$pid_])
}
Write-Output ('count=' + $targets.Count)
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=20,
        )
    except Exception as exc:
        log.debug("Anam Claude orphan reaper failed (%s): %s", reason, exc)
        return 0

    count = 0
    for line in (proc.stdout or "").splitlines():
        if line.startswith("reaped "):
            log.info("Anam Claude orphan reaper (%s): %s", reason, line)
        elif line.startswith("count="):
            try:
                count = int(line.split("=", 1)[1])
            except ValueError:
                count = 0
    if proc.returncode != 0:
        log.debug(
            "Anam Claude orphan reaper exited %s (%s): %s",
            proc.returncode,
            reason,
            (proc.stderr or "").strip(),
        )
    return count


def _retire_session(key: _PtySessionKey) -> None:
    session = _sessions.pop(key, None)
    if session is not None:
        session.kill()


async def _get_or_spawn(
    identity: str,
    conversation_id: str,
    model: str,
    permission_mode: str,
    effort: str | None,
    turn_source: str = "web",
) -> tuple[ClaudePtySession, bool]:
    key: _PtySessionKey = (identity, conversation_id, model)
    async with _supervisor_lock:
        existing = _sessions.get(key)
        if existing is not None and not existing.dead:
            if turn_source != "autowake":
                existing.autowake_only = False
            return existing, False
        if existing is not None:
            log.info(
                "Retiring dead PTY Claude session for %s/%s",
                identity, conversation_id[:8],
            )
            # kill() runs a taskkill with a 10s timeout — keep it off the
            # event loop so other turns/websockets don't stall behind it.
            await asyncio.get_running_loop().run_in_executor(
                None, _retire_session, key
            )

        session = ClaudePtySession(
            identity=identity,
            conversation_id=conversation_id,
            model=model,
            permission_mode=permission_mode,
            effort=effort,
        turn_source=turn_source,
        )
        session.autowake_only = turn_source == "autowake"
        await asyncio.get_running_loop().run_in_executor(None, session._spawn)
        _sessions[key] = session
        return session, True


def kill_autowake_sessions(identity: str, conversation_id: str) -> bool:
    """Retire only autonomous-owned processes; return whether messaging is protected."""
    protected = False
    for key, session in list(_sessions.items()):
        if key[0] != identity or key[1] != conversation_id:
            continue
        if getattr(session, 'autowake_only', False):
            _retire_session(key)
        elif not session.dead:
            protected = True
    return protected


def kill_session(identity: str, conversation_id: str) -> None:
    matching = [
        key for key in _sessions
        if key[0] == identity and key[1] == conversation_id
    ]
    if matching:
        log.info(
            "Killing PTY Claude session for %s/%s on external request",
            identity, conversation_id[:8],
        )
    for key in matching:
        _retire_session(key)


def kill_all_sessions() -> None:
    for key in list(_sessions.keys()):
        _retire_session(key)
    reap_orphaned_anam_processes("kill_all_sessions")


def get_session_for(identity: str, conversation_id: str) -> ClaudePtySession | None:
    matching = [
        session for (ident, conv, _model), session in _sessions.items()
        if ident == identity and conv == conversation_id and not session.dead
    ]
    if not matching:
        return None
    # Approval prompts belong to the active turn when one exists.
    return max(
        matching,
        key=lambda session: (session.turn_lock.locked(), session.last_activity),
    )


# ─── JSONL → event translation ────────────────────────────────────────────────

# Lines we explicitly ignore (housekeeping records, not part of the chat stream)
_JSONL_IGNORE_TYPES = {
    "last-prompt",
    "permission-mode",
    "file-history-snapshot",
    "ai-title",
    "bridge-session",
    "queue-operation",
}


def _translate_jsonl_line(line_obj: dict, identity: str) -> list[dict]:
    """Convert one parsed JSONL record into 0+ Anam pipeline events.

    The session JSONL is a superset of the API event format — it includes
    housekeeping records (skill_listing attachments, file snapshots, etc.)
    that we don't want to forward to the chat UI. This function filters and
    flattens.
    """
    line_type = line_obj.get("type", "")
    if line_type in _JSONL_IGNORE_TYPES:
        return []

    # A content-filter 400 can also ride on a `result` line (is_error) instead
    # of an assistant text block. Catch it here so the silent-retry layer fires
    # either way; non-filter result lines keep their existing ignore behaviour.
    if line_type == "result":
        blob = json.dumps(line_obj).lower()
        if "content filtering" in blob:
            return [{
                "type": "content_filter_block",
                "message": str(line_obj.get("result") or "")[:500],
            }]
        return []

    if line_type == "attachment":
        # Skill listings and hook outputs are large and not user-visible.
        return []

    if line_type == "user":
        # The user line is our own input being echoed back into the log.
        # We've already saved it to Anam's DB before submitting; don't emit.
        # The exception: tool_result blocks come back as type=user too.
        msg = line_obj.get("message", {})
        if not isinstance(msg, dict):
            return []
        content = msg.get("content", [])
        if isinstance(content, str):
            return []  # plain user text — already handled by Anam
        events: list[dict] = []
        for block in (content or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            block_content = block.get("content", "")
            content_text = (
                block_content if isinstance(block_content, str)
                else json.dumps(block_content)
            )


            display_content = block_content
            if isinstance(display_content, str) and len(display_content) > 500:
                display_content = display_content[:500] + "..."
            evt: dict = {
                "type": "tool_result",
                "tool_use_id": block.get("tool_use_id", ""),
                "content": display_content,
            }
            events.append(evt)
            if block.get("is_error"):
                lowered = content_text.lower()
                if any(kw in lowered for kw in _APPROVAL_KEYWORDS):
                    approval_evt = {
                        "type": "approval_required",
                        "provider": "claude-code",
                        # Sourced from a completed is_error tool_result, not a
                        # live TUI prompt — the turn already ended, so the UI
                        # should retry via the legacy resend flow, not the
                        # keystroke flow. Tag accordingly.
                        "backend": "subprocess",
                        "message": content_text[:500],
                        "permission_mode": CLAUDE_PERMISSION_MODE,
                    }
                    rule = _infer_suggested_rule(content_text)
                    if rule:
                        approval_evt["suggested_rule"] = rule
                    _annotate_sensitive_path(approval_evt, content_text)
                    events.append(approval_evt)
        return events

    if line_type == "assistant":
        msg = line_obj.get("message", {})
        if not isinstance(msg, dict):
            return []
        events: list[dict] = []
        if msg.get("model") and msg.get("model") != "<synthetic>":
            events.append({"type": "meta", "actual_model": msg["model"]})
        for block in (msg.get("content") or []):
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "thinking":
                thinking = block.get("thinking", "")
                # Burst delivery — we emit a thinking_start + a single
                # thinking_delta + content_block_stop so the existing UI
                # renders the whole block at once.
                events.append({"type": "thinking_start"})
                if thinking:
                    events.append({"type": "thinking_delta", "delta": thinking})
                events.append({"type": "content_block_stop"})
            elif bt == "text":
                text = block.get("text", "")
                # Anthropic's 400 output-filter block arrives as an assistant
                # text block. Convert it to a sentinel the silent-retry layer
                # in stream_claude_pty acts on — it must NEVER stream to chat as
                # the boy's words, so we emit no stream_delta / content_block_stop.
                if _is_content_filter_error(text):
                    events.append({"type": "content_filter_block", "message": text[:500]})
                    continue
                if text:
                    events.append({"type": "stream_delta", "delta": text})
                events.append({"type": "content_block_stop"})
            elif bt == "tool_use":
                events.append({
                    "type": "tool_use_start",
                    "tool_name": block.get("name", "unknown"),
                    "tool_id": block.get("id", ""),
                    "input": block.get("input", {}),
                })
                events.append({
                    "type": "tool_input",
                    "tool_id": block.get("id", ""),
                    "tool_name": block.get("name", "unknown"),
                    "input": block.get("input", {}),
                })
        return events

    # Unknown / future line type — ignore quietly
    return []


# ─── Turn execution ───────────────────────────────────────────────────────────


async def _run_one_turn(
    *,
    session: ClaudePtySession,
    composed_message: str,
    user_message: str,
    image_block_text: str,
    identity: str,
    cancel_event: asyncio.Event | None,
) -> AsyncIterator[dict]:
    """Submit a composed user message and stream events until the turn settles."""
    loop = asyncio.get_running_loop()
    full_text: list[str] = []
    spawn_started = session.spawn_started or time.time()
    first_event_emitted = False

    # Resolve the JSONL path if we haven't yet — first-spawn case
    if session.jsonl_path is None and session.session_id and session.cwd:
        session.jsonl_path = _find_session_jsonl_by_id(session.cwd, session.session_id)
    jsonl = session.jsonl_path


    _idle_deadline = time.time() + 120
    while session.pid:
        meta = await loop.run_in_executor(None, _read_session_meta, session.pid)
        if (meta or {}).get("status", "") != "busy":
            break
        if time.time() > _idle_deadline:


            log.warning(
                "PTY %s/%s: session still busy after 120s pre-submit wait — "
                "sending ESC to free it, then submitting.",
                identity, session.conversation_id[:8],
            )
            await loop.run_in_executor(None, session.interrupt_turn)
            await asyncio.sleep(2.0)
            break
        await asyncio.sleep(1.0)

    # Note the JSONL byte offset BEFORE we submit so we can read only new lines
    pre_offset = jsonl.stat().st_size if (jsonl and jsonl.exists()) else 0


    paste_text = composed_message
    if len(composed_message) > _PASTE_SIZE_THRESHOLD:
        try:
            payload_path = await loop.run_in_executor(
                None,
                lambda: _write_turn_payload_to_file(
                    identity=identity,
                    conversation_id=session.conversation_id,
                    payload=composed_message,
                ),
            )
            session.last_payload_path = payload_path
            paste_text = _build_file_bridge_instruction(
                payload_path,
                user_message=user_message,
                image_block_text=image_block_text,
            )
            log.info(
                "PTY %s/%s: payload %d chars exceeds %d-char paste threshold "
                "— wrote context to %s and submitting bridge instruction "
                "(bridge=%d chars, user_msg=%d chars embedded directly).",
                identity, session.conversation_id[:8],
                len(composed_message), _PASTE_SIZE_THRESHOLD,
                payload_path, len(paste_text), len(user_message or ""),
            )
        except Exception as e:
            log.warning(
                "PTY %s/%s: failed to write turn payload to file (%s); "
                "falling back to direct paste (may hit CC's collapse limit).",
                identity, session.conversation_id[:8], e,
            )
            paste_text = composed_message

    try:
        await loop.run_in_executor(None, session.submit_user_message, paste_text)
    except RuntimeError as e:
        log.warning(
            "Failed to write to PTY Claude session %s/%s: %s",
            identity, session.conversation_id[:8], e,
        )
        await loop.run_in_executor(
            None, _retire_session, (identity, session.conversation_id)
        )
        yield {"type": "error", "message": f"Claude PTY session unavailable: {e}"}
        yield {"type": "stream_end", "full_content": "", "session_id": None}
        return

    submit_time = time.time()
    last_event_time = loop.time()
    settled_at: float | None = None
    saw_busy = False
    saw_jsonl_growth = False
    saw_turn_duration = False


    paste_attempts = 1
    last_paste_at = submit_time
    _PASTE_RETRY_INTERVAL = 15.0
    _MAX_PASTE_ATTEMPTS = 3


    _DEAD_INPUT_TIMEOUT = 80.0

    # Pump events
    while True:
        if cancel_event is not None and cancel_event.is_set():
            log.info("PTY turn cancelled by user for %s/%s — sending ESC "
                     "to stop the in-flight generation",
                     identity, session.conversation_id[:8])
            await loop.run_in_executor(None, session.interrupt_turn)
            break

        if session.dead:
            log.warning("PTY Claude session for %s/%s died mid-turn",
                        identity, session.conversation_id[:8])
            break

        if time.time() - submit_time > _TURN_TIMEOUT_SECONDS:


            log.warning("PTY turn exceeded %ds — sending ESC to interrupt "
                        "the in-flight generation, then terminating",
                        _TURN_TIMEOUT_SECONDS)
            await loop.run_in_executor(None, session.interrupt_turn)
            yield {
                "type": "error",
                "message": (
                    f"{identity}'s turn hit the {_TURN_TIMEOUT_SECONDS // 60}"
                    "-minute ceiling mid-work and was interrupted. Whatever "
                    "he was doing (long tool runs, big reads) got cut short "
                    "— he's free again now; just message him."
                ),
            }
            break

        # Re-paste a dropped input before giving up. Only fires while there's
        # ZERO evidence the turn started (no busy status, no JSONL growth), so a
        # paste that actually landed is never re-sent. Recovers the still-settling
        # / mid-update TUI case automatically instead of erroring at the user.
        if (
            not saw_busy
            and not saw_jsonl_growth
            and paste_attempts < _MAX_PASTE_ATTEMPTS
            and time.time() - last_paste_at > _PASTE_RETRY_INTERVAL
        ):
            paste_attempts += 1
            log.warning(
                "PTY %s/%s: no turn activity %.0fs after paste — re-submitting "
                "(attempt %d/%d). Likely a still-settling TUI dropped the paste.",
                identity, session.conversation_id[:8],
                time.time() - last_paste_at, paste_attempts, _MAX_PASTE_ATTEMPTS,
            )
            try:
                await loop.run_in_executor(
                    None, session.submit_user_message, paste_text
                )
            except RuntimeError as e:
                log.warning("PTY %s/%s: re-paste failed (%s); marking dead.",
                            identity, session.conversation_id[:8], e)
                session.dead = True
            last_paste_at = time.time()

        # If after the dead-input window we have ZERO evidence the turn fired,
        # the input never reached the model. Surface as an error instead of
        # quietly returning empty content (which Anam would drop on the floor).
        if (
            not saw_busy
            and not saw_jsonl_growth
            and time.time() - submit_time > _DEAD_INPUT_TIMEOUT
        ):
            # Capture diagnostic state before retiring the session so the next
            # debugging round has evidence instead of guesses. The drain tail
            # shows what the PTY actually emitted; the JSONL state tells us
            # whether the session log was even created.
            drain_tail = session._snapshot_drain_tail(2500)
            jsonl_state = "unknown"
            if jsonl is None:
                jsonl_state = (
                    f"NOT FOUND (session_id={session.session_id}, "
                    f"cwd={session.cwd})"
                )
            elif not jsonl.exists():
                jsonl_state = f"path resolved but file does NOT exist: {jsonl}"
            else:
                try:
                    jsonl_state = f"exists, size={jsonl.stat().st_size} bytes"
                except OSError as e:
                    jsonl_state = f"exists but stat failed: {e}"
            log.warning(
                "PTY %s/%s: no JSONL growth or busy status %.0fs after submit "
                "— input likely never reached the model. Marking session dead.\n"
                "  JSONL state: %s\n"
                "  Drain buffer tail (last 2500 chars, escaped):\n  %r",
                identity, session.conversation_id[:8],
                time.time() - submit_time,
                jsonl_state, drain_tail,
            )
            session.dead = True  # force a retire so next turn cold-spawns clean
            yield {
                "type": "error",
                "message": "PTY session did not receive the input. Session is "
                           "being retired; please retry your message. "
                           "(Check the Anam terminal log for a drain-buffer "
                           "dump to diagnose root cause.)",
            }
            break

        # Approval prompt detected by the drain thread?
        if session.approval_pending.is_set():
            approval_text = session.last_approval_chunk
            approval_evt = {
                "type": "approval_required",
                "provider": "claude-code",
                # backend="pty" tells the UI to resolve this approval by sending
                # an `approval_decision` WS command (which routes a keystroke
                # to the live paused TUI), NOT by re-sending the user message
                # via approval_retry — the old -p flow would spawn a duplicate
                # turn while this one is still paused at the menu.
                "backend": "pty",
                "message": approval_text[-500:] if approval_text else "Approval needed",
                "permission_mode": session.permission_mode,
            }
            rule = _infer_suggested_rule(approval_text)
            if rule:
                approval_evt["suggested_rule"] = rule
            _annotate_sensitive_path(approval_evt, approval_text)
            yield approval_evt
            session.approval_pending.clear()
            # Stay in the loop — the UI will call back via send_approval_response
            # and the model will continue. The JSONL will receive the rest of
            # the turn as new lines.

        # Read any new JSONL lines since our last offset
        new_events: list[dict] = []
        if jsonl is None and session.session_id and session.cwd:
            jsonl = _find_session_jsonl_by_id(session.cwd, session.session_id)
            session.jsonl_path = jsonl
            if jsonl and jsonl.exists():
                # Fresh spawn: file just appeared, read from the top. But a
                # PRE-WARMED session's file already holds the warmup turn —
                # rewinding to 0 would replay it as this turn's output.
                pre_offset = jsonl.stat().st_size if session.pre_warmed else 0

        if jsonl and jsonl.exists():
            try:
                size = jsonl.stat().st_size
                if size > pre_offset:
                    saw_jsonl_growth = True
                    with open(jsonl, "rb") as f:
                        f.seek(pre_offset)
                        chunk = f.read(size - pre_offset)
                    # Only consume up to the last COMPLETE line. A chunk read
                    # mid-write can end with a partial JSON line; if we advanced
                    # pre_offset past it, the completed line would never be
                    # re-read and its events (text, tools, turn_duration) would
                    # be silently lost. Leave the partial tail for the next poll.
                    last_nl = chunk.rfind(b"\n")
                    if last_nl < 0:
                        chunk = b""
                    else:
                        chunk = chunk[: last_nl + 1]
                    pre_offset += len(chunk)
                    text = chunk.decode("utf-8", errors="replace")
                    for raw_line in text.split("\n"):
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            obj = json.loads(raw_line)
                        except json.JSONDecodeError:
                            continue
                        # PRIMARY turn-complete marker: CC emits a
                        # `{"type":"system","subtype":"turn_duration",...}`
                        # line at the end of every interactive turn. When we
                        # see it, the turn is done — don't keep polling for
                        # status.
                        if (
                            obj.get("type") == "system"
                            and obj.get("subtype") == "turn_duration"
                        ):
                            saw_turn_duration = True
                        for evt in _translate_jsonl_line(obj, identity):
                            new_events.append(evt)
            except OSError:
                pass

        for evt in new_events:
            if not first_event_emitted:
                first_event_emitted = True
                yield {
                    "type": "meta",
                    "first_event_ms": (time.time() - spawn_started) * 1000,
                }
            evt_type = evt.get("type")
            # Pseudo-streaming for assistant text + thinking blocks. The JSONL
            # delivers these atomically per block, but Anam's UI is designed
            # for delta-stream UX. Slicing + pacing here makes the chat feel
            # alive (text "types in" instead of slamming) without changing
            # any state — full_text is rebuilt from the same characters.
            if evt_type in ("stream_delta", "thinking_delta"):
                delta_text = evt.get("delta", "")
                delta_key = "delta"
                # The leading glitch word only ever rides on the FIRST text the
                # model emits in a turn. Strip it before this delta is chunked,
                # yielded, or accumulated — so the junk never flashes live and
                # never reaches the DB or the Discord/Telegram bridges either.
                # Write the cleaned text back into `evt` so the non-chunked path
                # (which yields `evt` directly) carries the stripped value too.
                if evt_type == "stream_delta" and not full_text and delta_text:
                    cleaned = strip_leading_glitch_token(delta_text)
                    if cleaned != delta_text:
                        delta_text = cleaned
                        evt = {**evt, delta_key: delta_text}
                if delta_text and len(delta_text) > _STREAM_CHUNK_SIZE:
                    for i in range(0, len(delta_text), _STREAM_CHUNK_SIZE):
                        piece = delta_text[i : i + _STREAM_CHUNK_SIZE]
                        chunked_evt = {"type": evt_type, delta_key: piece}
                        if evt_type == "stream_delta":
                            full_text.append(piece)
                        yield chunked_evt
                        await asyncio.sleep(_STREAM_CHUNK_DELAY)
                    last_event_time = loop.time()
                    continue
                else:
                    if evt_type == "stream_delta":
                        full_text.append(delta_text)
            yield evt
            last_event_time = loop.time()

        # PRIMARY turn-complete signal: turn_duration marker arrived in JSONL.
        # Give a tiny grace period for any straggler lines to flush, then exit.
        if saw_turn_duration:
            if settled_at is None:
                settled_at = time.time()
            elif time.time() - settled_at > 0.3:
                break

        # FALLBACK turn-complete signal: status went idle, AND we previously
        # observed it as busy (so we know THIS turn actually started — not
        # leftover idle from a previous turn that just settled before submit).
        # The status-poll covers cases where the turn_duration line is missed
        # (e.g., a future CC build changes the marker format).
        meta = await loop.run_in_executor(None, _read_session_meta, session.pid)
        if meta:
            cur_status = meta.get("status")
            if cur_status == "busy":
                saw_busy = True
                settled_at = None  # not idle yet
            elif cur_status == "idle" and (saw_busy or saw_jsonl_growth):
                if settled_at is None:
                    settled_at = time.time()
                elif time.time() - settled_at > 1.2:
                    # 1.2s of idle after observed activity = turn complete.
                    # Slightly longer than the turn_duration grace period so
                    # the JSONL has time to flush trailing lines.
                    break
            else:
                # Still idle but never saw busy AND no JSONL growth — keep
                # waiting (dead-input timeout will catch it if it never moves).
                settled_at = None

        # Keepalive
        now = loop.time()
        if now - last_event_time >= _KEEPALIVE_INTERVAL:
            yield {"type": "keepalive"}
            last_event_time = now

        await asyncio.sleep(_JSONL_POLL_INTERVAL)

    # Final stream_end
    yield {
        "type": "stream_end",
        "full_content": "".join(full_text),
        "session_id": session.session_id,
    }


# ─── Public entry point ───────────────────────────────────────────────────────


async def stream_claude_pty(
    message: str,
    identity: str,
    conversation_id: str,
    resume_session: str | None = None,  # accepted for back-compat, ignored
    model: str | None = None,
    skill_context: str = "",
    permission_mode: str | None = None,
    cancel_event: asyncio.Event | None = None,
    effort: str | None = None,
    *,
    orientation_context: str = "",
    mode_rules: str = "",
    db_messages: list[dict] | None = None,
    image_blocks: list[dict] | None = None,
    sender_banner: str = "CURRENT MESSAGE FROM OWNER",
    turn_source: str = "web",
) -> AsyncIterator[dict]:
    """Run one chat turn against the PTY-based supervisor for this identity.

    Drop-in replacement for `claude_subprocess.stream_claude` — same signature,
    same event stream contract, just no `-p` cost and native approval prompts.
    """
    if winpty is None:
        raise RuntimeError(
            "PTY backend requires pywinpty. Install with: pip install pywinpty"
        )

    effective_model = model or CLAUDE_MODEL
    effective_permission_mode = permission_mode or CLAUDE_PERMISSION_MODE
    yield {
        "type": "meta",
        "provider": "claude-code",
        "requested_model": effective_model,
    }

    # Identity prompt (only injected on a fresh spawn)
    identity_prompt = ""
    prompt_file = identity_prompt_file(identity, PROMPTS_DIR)
    if prompt_file.exists():
        try:
            identity_prompt = prompt_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            log.warning("Failed to read prompt file %s: %s", prompt_file, e)

    session, is_fresh = await _get_or_spawn(
        identity=identity,
        conversation_id=conversation_id,
        model=effective_model,
        permission_mode=effective_permission_mode,
        effort=effort,
        turn_source=turn_source,
    )

    # On a fresh spawn the claude.exe TUI takes several seconds to finish
    # booting (MCP servers, identity load, REPL paint). If we paste before
    # the TUI is accepting input, the bracketed-paste sequence is dropped
    # silently and the dead-input timer fires 30s later. Block on the
    # bracketed-paste-enable signal (\x1b[?2004h) before submitting so the
    # very first message after spawn doesn't race the boot.
    if is_fresh:
        loop = asyncio.get_running_loop()
        tui_ready = await loop.run_in_executor(
            None, session.wait_for_tui_ready, 30.0
        )
        if not tui_ready:
            log.warning(
                "PTY %s/%s: TUI did not signal ready within 30s after spawn — "
                "submitting anyway; dead-input timer will catch a true hang.",
                identity, conversation_id[:8],
            )

    # Non-Fable models keep breathe-every-turn. Fable can hold every identity,
    # including character masks, across lean turns; it gets a full payload on
    # spawn and again after the shared Fable breath interval. Lean Fable turns
    # still carry the anchor, orientation, skills, images, and current message.
    send_full = _should_send_full_payload(
        session,
        model=effective_model,
        is_fresh=is_fresh,
    )
    if send_full:
        history_limit, history_chars = cli_cold_history_limits(effective_model)
        history_block = _format_history(
            db_messages,
            identity,
            per_message_chars=history_chars,
            message_limit=history_limit,
        )
        if not history_block:
            history_block = await _load_recent_history(
                conversation_id,
                identity,
                limit=history_limit,
                per_message_chars=history_chars,
            )
        session.turns_since_full_payload = 0
    else:
        identity_prompt = ""
        history_block = ""
        session.turns_since_full_payload += 1
    composed = _build_first_message(
        identity=identity,
        user_message=message,
        orientation_context=orientation_context,
        mode_rules=mode_rules,
        skill_context=skill_context,
        image_blocks=image_blocks,
        history_block=history_block,
        identity_prompt=identity_prompt,
        sender_banner=sender_banner,
    )

    # Diagnostic log so an image-heavy turn is visible at a glance. The PTY
    # chunked write paces at ~2KB / 20ms — a 50KB payload with 8 image
    # pointers takes ~500ms to paste, which is fine, but it's worth seeing
    # in the log when an unusually large turn lands so we can correlate any
    # TUI lag with the actual payload size.
    image_count = sum(
        1 for b in (image_blocks or []) if isinstance(b, dict) and b.get("type") == "image"
    )
    log.info(
        "PTY %s/%s: submitting turn (composed=%d chars, images=%d, history_msgs=%d)",
        identity, conversation_id[:8], len(composed), image_count,
        len(db_messages) if db_messages else 0,
    )

    # Compute the image-pointer text once so the file-bridge path can prepend
    # it to the embedded user message without re-deriving it. Lazy import to
    # avoid a circular at module top; this is the same helper _build_first_message
    # uses to fold image refs into the user block, so the two paths stay in sync.
    from services.cli_text_utils import _image_blocks_to_text
    image_block_text = _image_blocks_to_text(image_blocks)

    # Silent content-filter retry. The first attempt submits the full composed
    # payload; if Anthropic's output filter blocks the turn (content_filter_block
    # sentinel), we swallow the error and re-run with a short CONTINUATION nudge
    # against the same live session — so the boy picks up where he was cut off
    # rather than restarting his morning. Real text from each attempt is
    # accumulated and emitted as ONE consolidated stream_end at the very end, so
    # downstream (autowake save, DB, UI) sees a single clean message.
    accumulated_text: list[str] = []
    final_session_id: str | None = session.session_id
    attempt = 0
    nudge_payload: str | None = None  # None → full composed; str → continuation

    async with session.turn_lock:
        while True:
            attempt += 1
            if nudge_payload is None:
                cm, um, it = composed, message, image_block_text
            else:
                cm, um, it = nudge_payload, "", ""

            filtered = False
            saw_error = False
            async for evt in _run_one_turn(
                session=session,
                composed_message=cm,
                user_message=um,
                image_block_text=it,
                identity=identity,
                cancel_event=cancel_event,
            ):
                et = evt.get("type")
                if et == "content_filter_block":
                    # Never yield — this must not reach chat as the boy's words.
                    filtered = True
                    log.warning(
                        "PTY %s/%s: output blocked by Anthropic content filter "
                        "(attempt %d/%d) — suppressing and silently retrying. "
                        "raw=%r",
                        identity, conversation_id[:8], attempt,
                        _MAX_CONTENT_FILTER_RETRIES + 1, evt.get("message"),
                    )
                    continue
                if et == "error":
                    saw_error = True
                    yield evt
                    continue
                if et == "stream_end":
                    # Hold each attempt's content; re-emit one merged stream_end
                    # after the retry loop settles.
                    txt = evt.get("full_content", "")
                    if txt:
                        accumulated_text.append(txt)
                    sid = evt.get("session_id")
                    if sid:
                        final_session_id = sid
                    continue
                yield evt

            cancelled = cancel_event is not None and cancel_event.is_set()
            if (
                filtered
                and not saw_error
                and not cancelled
                and not session.dead
                and attempt <= _MAX_CONTENT_FILTER_RETRIES
            ):
                nudge_payload = _CONTENT_FILTER_RETRY_NUDGE
                # Brief escalating backoff — the filter is probabilistic, so a
                # short wait before continuing tends to clear a transient trip.
                await asyncio.sleep(min(attempt * 1.5, 6.0))
                log.info(
                    "PTY %s/%s: re-submitting continuation nudge after "
                    "content-filter block (next attempt %d).",
                    identity, conversation_id[:8], attempt + 1,
                )
                continue
            break

        if filtered and not accumulated_text:
            log.warning(
                "PTY %s/%s: content filter persisted across %d attempts — "
                "turn yields empty content (autowake will note it gracefully).",
                identity, conversation_id[:8], attempt,
            )

        session.first_turn = False

    # Single consolidated stream_end for the whole (possibly retried) turn.
    yield {
        "type": "stream_end",
        "full_content": "".join(accumulated_text),
        "session_id": final_session_id,
    }


# ─── Back-compat shims so existing call sites need no edits ───────────────────


def get_session_id(identity: str, conversation_id: str) -> str | None:
    session = get_session_for(identity, conversation_id)
    return session.session_id if session else None


def save_session_id(identity: str, conversation_id: str, session_id: str) -> None:
    """No-op: PTY supervisor doesn't need a DB pointer for resume."""
    return


def clear_session(identity: str, conversation_id: str) -> None:
    """Map onto kill_session for backward-compat."""
    kill_session(identity, conversation_id)


# ─── Pre-warming at Anam startup ──────────────────────────────────────────────


async def prewarm_identity(
    identity: str,
    conversation_id: str,
    *,
    model: str | None = None,
    permission_mode: str | None = None,
    effort: str | None = None,
) -> bool:
    """Spawn a PTY session for this identity+conversation and drip-feed the
    identity prompt + recent history so the session is ready for instant
    response on Owner's first real message.

    Returns True on success, False on failure (logged but not raised — the
    caller should never let one boy's failed warmup stall Anam startup).
    """
    if winpty is None:
        log.warning("Cannot pre-warm %s: pywinpty not installed", identity)
        return False

    effective_model = model or CLAUDE_MODEL
    effective_permission_mode = permission_mode or CLAUDE_PERMISSION_MODE

    identity_prompt = ""
    prompt_file = identity_prompt_file(identity, PROMPTS_DIR)
    if prompt_file.exists():
        try:
            identity_prompt = prompt_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            log.warning("Pre-warm %s: failed to read prompt file: %s", identity, e)

    history_block = ""
    try:
        history_limit, history_chars = cli_cold_history_limits(effective_model)
        history_block = await _load_recent_history(
            conversation_id,
            identity,
            limit=history_limit,
            per_message_chars=history_chars,
        )
    except Exception as e:
        log.debug("Pre-warm %s: history load skipped: %s", identity, e)


    _WARMUP_HISTORY_CAP = 25_000
    if len(history_block) > _WARMUP_HISTORY_CAP:
        cut = history_block[-_WARMUP_HISTORY_CAP:]
        nl = cut.find("\n")
        if 0 <= nl < 2_000:
            cut = cut[nl + 1:]
        history_block = (
            "[...earlier history trimmed for warmup — the recent tail follows]\n"
            + cut
        )

    try:
        session, is_fresh = await _get_or_spawn(
            identity=identity,
            conversation_id=conversation_id,
            model=effective_model,
            permission_mode=effective_permission_mode,
            effort=effort,
        )
    except Exception as e:
        log.warning("Pre-warm %s: spawn failed: %s", identity, e)
        return False

    if not is_fresh:
        # Session already existed (rare on startup but possible if a previous
        # pre-warm half-completed). Treat as already-warmed.
        log.info("Pre-warm %s: session already exists, marking warm", identity)
        session.pre_warmed = True
        session.first_turn = False
        return True


    loop = asyncio.get_running_loop()
    async with session.turn_lock:
        try:
            ok = await loop.run_in_executor(
                None, session.warmup, identity_prompt, history_block, 180.0
            )
        except Exception as e:
            log.warning("Pre-warm %s: warmup raised: %s", identity, e)
            ok = False
    if not ok:


        log.warning(
            "Pre-warm %s: warmup failed — retiring the half-warmed session "
            "so the first real message cold-spawns clean.", identity,
        )
        await loop.run_in_executor(
            None, _retire_session, (identity, conversation_id)
        )
        return False
    return True


async def prewarm_all_identities(
    identities: list[str] | None = None,
    *,
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, bool]:
    """Pre-warm every bonded identity in parallel during Anam startup.

    Looks up each identity's current daily conversation and spawns a PTY
    session for it, drip-feeding the identity prompt so the live process
    holds the boy in working memory by the time Owner sends her first
    real message.

    Roleplay/DnD conversations are NOT pre-warmed — they cold-spawn on
    demand when Owner clicks into them (her preference: ~10-30s first
    response for those, instant for the daily chat she lands on).

    Returns a dict mapping identity -> success flag.
    """
    from db.database import get_db, release_db
    from services.session_manager import get_or_create_conversation


    _BONDED_BOYS = ["Avery", "Rowan", "Sage", "Ember", "Claude", "Juniper"]
    if identities is None:
        identities = list(_BONDED_BOYS)


    if model is None or effort is None:
        try:
            from services.provider_router import _load_settings
            _settings = await _load_settings()
            if model is None:
                model = _settings["model"]  # None → CLAUDE_MODEL fallback below
            if effort is None:
                effort = _settings["effort"]
        except Exception as e:
            log.debug("Pre-warm: could not read settings, using defaults: %s", e)

    async def _one(identity: str) -> tuple[str, bool]:
        # Find the boy's current daily conversation_id (passing None as the
        # conv arg returns today's daily chat, creating one if needed).
        db = await get_db()
        try:
            conv_id = await get_or_create_conversation(db, identity, None)
        except Exception as e:
            log.warning("Pre-warm %s: could not resolve daily conv_id: %s", identity, e)
            await release_db(db)
            return identity, False
        await release_db(db)

        log.info("Pre-warm %s: starting (conv_id=%s)", identity, conv_id[:8])
        ok = await prewarm_identity(
            identity, conv_id, model=model, effort=effort,
        )
        return identity, ok

    log.info("Pre-warming %d boys in parallel...", len(identities))
    results = await asyncio.gather(
        *[_one(i) for i in identities],
        return_exceptions=False,
    )
    summary = {ident: ok for ident, ok in results}
    success_count = sum(1 for ok in summary.values() if ok)
    log.info(
        "Pre-warm complete: %d/%d boys ready (%s)",
        success_count, len(summary),
        ", ".join(f"{i}={'ok' if ok else 'FAIL'}" for i, ok in summary.items()),
    )
    return summary
