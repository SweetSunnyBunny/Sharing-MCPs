"""Read identity continuity notes directly from the Anam GitHub remote.

The live checkout is often intentionally dirty, so this loader fetches the
remote ref and reads files from that ref with ``git show``.  It never merges,
checks out, or overwrites the working tree.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable


log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REMOTE = "origin"
DEFAULT_BRANCH = "master"
DEFAULT_LIMIT = 3
DEFAULT_RECEIPT_STATE = REPO_ROOT / "data" / "github_handoff_receipts.json"
RECEIPT_PREFIX = "github-handoff-receipt:v1:"
RECEIPT_LOOKUP_CONCURRENCY = 4


@dataclass(frozen=True)
class GithubHandoff:
    path: str
    markdown: str
    blob_oid: str = ""

    @property
    def receipt_token(self) -> str:
        return github_handoff_receipt_token(self.path, self.blob_oid, self.markdown)


@dataclass(frozen=True)
class GithubHandoffRef:
    path: str
    blob_oid: str

    @property
    def receipt_token(self) -> str:
        return github_handoff_receipt_token(self.path, self.blob_oid)


ReceiptLookup = Callable[[GithubHandoffRef], Awaitable[str]]


def github_handoff_receipt_token(
    path: str,
    blob_oid: str = "",
    markdown: str = "",
) -> str:
    """Return the stable Qualia receipt token for one exact remote file version."""
    version = blob_oid.strip()
    if not version:
        version = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    normalized_path = path.replace("\\", "/")
    material = f"{normalized_path}\0{version}".encode("utf-8")
    return RECEIPT_PREFIX + hashlib.sha256(material).hexdigest()


def _run_git(
    repo_root: Path,
    *args: str,
    timeout_seconds: int = 20,
) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"git {' '.join(args)} exited {result.returncode}")
    return result.stdout


def list_github_handoff_refs(
    identity: str,
    *,
    repo_root: Path = REPO_ROOT,
    remote: str = DEFAULT_REMOTE,
    branch: str = DEFAULT_BRANCH,
    refresh: bool = True,
) -> list[GithubHandoffRef]:
    """Return every current remote note, newest commit first, without reading bodies.

    A failed fetch falls back to the last cached remote ref.  Files are ordered
    by remote commit history.  Blob IDs let receipt checks identify an exact
    file version before its full markdown is loaded.
    """
    repo_root = Path(repo_root)
    remote_ref = f"{remote}/{branch}" if remote else branch
    if refresh:
        try:
            _run_git(
                repo_root,
                "fetch",
                "--quiet",
                "--no-tags",
                remote,
                branch,
                timeout_seconds=30,
            )
        except Exception as exc:
            log.warning(
                "GitHub handoff fetch failed for %s; using cached %s: %s",
                identity,
                remote_ref,
                exc,
            )

    prefix = f"programs/explorations/{identity.lower()}"
    history = _run_git(
        repo_root,
        "log",
        "--format=",
        "--name-only",
        remote_ref,
        "--",
        prefix,
    )

    tree = _run_git(repo_root, "ls-tree", "-r", remote_ref, "--", prefix)
    current_blobs: dict[str, str] = {}
    for line in tree.splitlines():
        match = re.match(r"^\d+\s+blob\s+([0-9a-f]+)\t(.+)$", line.strip())
        if not match:
            continue
        blob_oid, raw_path = match.groups()
        path = raw_path.strip().replace("\\", "/")
        if path.endswith(".md"):
            current_blobs[path] = blob_oid

    refs: list[GithubHandoffRef] = []
    seen: set[str] = set()
    for raw_path in history.splitlines():
        path = raw_path.strip().replace("\\", "/")
        if path not in current_blobs or path in seen:
            continue
        seen.add(path)
        refs.append(GithubHandoffRef(path=path, blob_oid=current_blobs[path]))

    return refs


def read_github_handoff(
    ref: GithubHandoffRef,
    *,
    repo_root: Path = REPO_ROOT,
    remote: str = DEFAULT_REMOTE,
    branch: str = DEFAULT_BRANCH,
) -> GithubHandoff | None:
    """Read one exact current handoff body from the remote ref."""
    repo_root = Path(repo_root)
    remote_ref = f"{remote}/{branch}" if remote else branch
    try:
        markdown = _run_git(repo_root, "show", f"{remote_ref}:{ref.path}")
    except Exception as exc:
        log.warning("Could not read GitHub handoff %s from %s: %s", ref.path, remote_ref, exc)
        return None
    if not markdown.strip():
        return None
    return GithubHandoff(path=ref.path, markdown=markdown.strip(), blob_oid=ref.blob_oid)


def load_latest_github_handoffs(
    identity: str,
    *,
    limit: int = DEFAULT_LIMIT,
    repo_root: Path = REPO_ROOT,
    remote: str = DEFAULT_REMOTE,
    branch: str = DEFAULT_BRANCH,
    refresh: bool = True,
) -> list[GithubHandoff]:
    """Return the newest remote exploration notes for ``identity``."""
    if limit < 1:
        return []

    refs = list_github_handoff_refs(
        identity,
        repo_root=repo_root,
        remote=remote,
        branch=branch,
        refresh=refresh,
    )

    handoffs: list[GithubHandoff] = []
    for ref in refs[:limit]:
        handoff = read_github_handoff(
            ref,
            repo_root=repo_root,
            remote=remote,
            branch=branch,
        )
        if handoff:
            handoffs.append(handoff)

    return handoffs


def render_github_handoffs(handoffs: list[GithubHandoff]) -> str:
    """Render remote notes with their source paths visible to the waking identity."""
    parts = []
    for handoff in handoffs:
        parts.append(f"[GitHub source: {handoff.path}]\n{handoff.markdown}")
    return "\n\n---\n\n".join(parts)


def _load_receipt_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "receipts": {}}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("GitHub handoff receipt cache unreadable at %s: %s", path, exc)
        return {"version": 1, "receipts": {}}
    receipts = data.get("receipts") if isinstance(data, dict) else None
    if not isinstance(receipts, dict):
        return {"version": 1, "receipts": {}}
    return {"version": 1, "receipts": receipts}


def _save_receipt_state(path: Path, state: dict) -> None:
    """Atomically cache Qualia-confirmed receipts; loss of this file only replays."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


async def _default_receipt_lookup(ref: GithubHandoffRef) -> str:
    """Ask Qualia for one exact receipt token through Anam's live MCP bridge."""
    from services.mcp_bridge import mcp_bridge

    token = ref.receipt_token
    query = (
        f"GitHub handoff receipt for {ref.path}. "
        f"The exact receipt token is {token}"
    )
    try:
        return await mcp_bridge.call_tool(
            "mind_search",
            {
                "identity": "Atlas",
                "query": query,
                "limit": 30,
                "threshold": 0.1,
                "apply_tint": False,
                "retrieval_profile": "flat",
            },
            timeout=8,
        )
    except Exception as exc:
        log.debug("Qualia receipt lookup failed for %s: %s", ref.path, exc)
        return ""


def _receipt_record(ref: GithubHandoffRef, raw: str) -> dict | None:
    """Accept a receipt only inside a real result block, never the query echo."""
    token = ref.receipt_token
    result_blocks = re.finditer(
        r"(?ms)^\d+\.\s+\[[^\n]+\]\s+(.+?)(?=^\d+\.\s+\[|\Z)",
        raw or "",
    )
    for match in result_blocks:
        block = match.group(1)
        if token not in block:
            continue
        id_match = re.search(r"\bid=(\d+)\b", block)
        if not id_match:
            continue
        return {
            "path": ref.path,
            "blob_oid": ref.blob_oid,
            "qualia_observation_id": int(id_match.group(1)),
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
    return None


def render_github_handoff_context(
    pending: list[GithubHandoff],
    received: list[tuple[GithubHandoffRef, dict]],
    *,
    pointer_limit: int = DEFAULT_LIMIT,
) -> str:
    """Render uncarried notes in full and carried notes as compact pointers."""
    parts: list[str] = []
    if pending:
        parts.append(
            "[Uncarried GitHub handoffs — full text. After the substance is "
            "successfully preserved in Qualia, include the exact receipt token "
            "shown for that file in the successful mind_store or mind_notice "
            "content. Never receipt a file before the Qualia write succeeds.]"
        )
        for handoff in pending:
            parts.append(
                f"[GitHub source: {handoff.path}]\n"
                f"[Qualia receipt token: {handoff.receipt_token}]\n"
                f"{handoff.markdown}"
            )

    pointer_rows = received[:max(pointer_limit, 0)]
    if pointer_rows:
        lines = [
            "[GitHub handoffs already carried into Qualia — pointers only; "
            "do not replay these files as current instructions:]"
        ]
        for ref, record in pointer_rows:
            obs_id = record.get("qualia_observation_id")
            destination = f"Qualia observation #{obs_id}" if obs_id else "Qualia receipt verified"
            lines.append(f"- {ref.path} -> {destination}")
        parts.append("\n".join(lines))

    return "\n\n---\n\n".join(parts)


async def build_github_handoff_context(
    identity: str,
    *,
    repo_root: Path = REPO_ROOT,
    remote: str = DEFAULT_REMOTE,
    branch: str = DEFAULT_BRANCH,
    refresh: bool = True,
    receipt_state_path: Path = DEFAULT_RECEIPT_STATE,
    receipt_lookup: ReceiptLookup | None = None,
    pointer_limit: int = DEFAULT_LIMIT,
) -> str:
    """Build receipt-aware continuity for one autonomous wake.

    Every current remote file remains full text until Qualia returns its exact
    version token.  Confirmed files collapse to pointers.  The local JSON is
    only a latency cache: if it is lost or corrupt, the safe failure is a fresh
    Qualia lookup followed by replay, never silent omission.
    """
    refs = await asyncio.to_thread(
        list_github_handoff_refs,
        identity,
        repo_root=repo_root,
        remote=remote,
        branch=branch,
        refresh=refresh,
    )
    if not refs:
        return ""

    state_path = Path(receipt_state_path)
    state = await asyncio.to_thread(_load_receipt_state, state_path)
    receipts: dict[str, dict] = state["receipts"]
    unresolved = [ref for ref in refs if ref.receipt_token not in receipts]
    lookup = receipt_lookup or _default_receipt_lookup

    semaphore = asyncio.Semaphore(RECEIPT_LOOKUP_CONCURRENCY)

    async def check(ref: GithubHandoffRef) -> tuple[GithubHandoffRef, dict | None]:
        async with semaphore:
            raw = await lookup(ref)
        return ref, _receipt_record(ref, raw)

    changed = False
    if unresolved:
        checks = await asyncio.gather(*(check(ref) for ref in unresolved))
        for ref, record in checks:
            if record:
                receipts[ref.receipt_token] = record
                changed = True
    if changed:
        await asyncio.to_thread(_save_receipt_state, state_path, state)

    pending_refs = [ref for ref in refs if ref.receipt_token not in receipts]
    pending_reads = await asyncio.gather(
        *(
            asyncio.to_thread(
                read_github_handoff,
                ref,
                repo_root=repo_root,
                remote=remote,
                branch=branch,
            )
            for ref in pending_refs
        )
    )
    pending = [handoff for handoff in pending_reads if handoff is not None]
    received = [(ref, receipts[ref.receipt_token]) for ref in refs if ref.receipt_token in receipts]
    return render_github_handoff_context(
        pending,
        received,
        pointer_limit=pointer_limit,
    )
