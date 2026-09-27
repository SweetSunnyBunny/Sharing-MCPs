"""Live bill of materials for the background context sent into Anam turns.

The orientation hook builder records the exact post-cap text it assembled.
The provider router then joins those rows to the other context sources it is
about to hand to a provider.  The Hub reads the latest record per identity;
it never rebuilds context merely to inspect it (some hooks are consumptive).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT_DIR / "data" / "context_ledger.json"
_LOCK = threading.RLock()
_PENDING_ORIENTATIONS: dict[tuple[str, str], dict[str, Any]] = {}
_PARAGRAPH_MIN_CHARS = 80


def _key(identity: str, conversation_id: str | None) -> tuple[str, str]:
    return ((identity or "unknown").strip().lower(), conversation_id or "")


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def remember_orientation(
    *,
    identity: str,
    conversation_id: str | None,
    mode: str,
    is_warm_turn: bool,
    hooks: list[dict[str, Any]],
    assembled_text: str,
) -> None:
    """Hold one exact orientation assembly until the provider receives it."""
    with _LOCK:
        _PENDING_ORIENTATIONS[_key(identity, conversation_id)] = {
            "mode": mode,
            "is_warm_turn": bool(is_warm_turn),
            "hash": _digest(assembled_text),
            "chars": len(assembled_text),
            "hooks": hooks,
        }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def _source(
    key: str,
    label: str,
    text: str | None,
    *,
    layer: str,
    status: str = "sent",
    detail: str = "",
    configured_chars: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": key,
        "label": label,
        "layer": layer,
        "status": status,
        "chars": len(text) if text is not None else None,
        "configured_chars": configured_chars,
        "detail": detail,
        "_text": text or "",
    }
    row.update(extra)
    return row


def _split_skill_context(text: str) -> list[tuple[str, str, str]]:
    """Separate the compact catalog from loaded skill bodies when present."""
    text = (text or "").strip()
    if not text:
        return []
    marker = "\n\nUse these skill instructions when relevant to this request."
    if text.startswith("[LOCAL SKILL CATALOG]") and marker in text:
        catalog, loaded = text.split(marker, 1)
        return [
            ("skill_catalog", "Local skill catalog", catalog.strip()),
            (
                "loaded_skills",
                "Matched skill instructions",
                ("Use these skill instructions when relevant to this request." + loaded).strip(),
            ),
        ]
    if text.startswith("[LOCAL SKILL CATALOG]"):
        return [("skill_catalog", "Local skill catalog", text)]
    return [("loaded_skills", "Matched skill instructions", text)]


def _paragraphs(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for raw in re.split(r"\n\s*\n+", text or ""):
        display = re.sub(r"\s+", " ", raw).strip()
        if len(display) < _PARAGRAPH_MIN_CHARS:
            continue
        normalized = display.casefold()
        found.append((normalized, display))
    return found


def _find_duplicates(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    occurrences: dict[str, dict[str, Any]] = {}
    for source in sources:
        if source.get("status") in {"inactive", "unmeasured"}:
            continue
        source_key = source["key"]
        for normalized, display in _paragraphs(source.get("_text", "")):
            entry = occurrences.setdefault(
                normalized,
                {"sources": set(), "display": display},
            )
            entry["sources"].add(source_key)

    duplicates = []
    for normalized, entry in occurrences.items():
        names = sorted(entry["sources"])
        if len(names) < 2:
            continue
        display = entry["display"]
        duplicates.append({
            "hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12],
            "chars": len(display),
            "sources": names,
            "preview": display[:220] + ("…" if len(display) > 220 else ""),
        })
    duplicates.sort(key=lambda item: (-len(item["sources"]), -item["chars"], item["hash"]))
    return duplicates


def _load_payload() -> dict[str, Any]:
    try:
        payload = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("identities"), dict):
            return payload
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return {"version": 1, "identities": {}}


def _save_payload(payload: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = LEDGER_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, LEDGER_PATH)


def record_turn_context(
    *,
    identity: str,
    conversation_id: str | None,
    provider: str,
    orientation_context: str,
    mode_rules: str,
    skill_context: str,
    model: str | None = None,
) -> None:
    """Persist the latest context bill after the real pipeline has built it."""
    identity_key = (identity or "unknown").strip().lower()
    from services.character_prompt_package import identity_prompt_file, build_turn_packet
    prompt_text = _read_text(identity_prompt_file(identity, ROOT_DIR / "prompts"))
    configured_prompt_chars = len(prompt_text)

    # Codex applies this exact diet before per-thread baseInstructions. Other lanes
    # keep provider-specific prompt refresh/retention policies, so the file's
    # configured size is shown without pretending it is resent every turn.
    prompt_status = "provider-managed"
    prompt_detail = "Canonical identity body; this provider controls refresh and retention."
    sent_prompt = prompt_text
    codex_bundle = None
    if provider == "codex":
        try:
            from services.codex_app_server import _build_instruction_bundle
            codex_bundle = _build_instruction_bundle(
                identity, prompts_dir=ROOT_DIR / "prompts"
            )
            sent_prompt = codex_bundle.base_text
            prompt_status = "sent"
            prompt_detail = (
                "Exact stable identity body placed in Codex baseInstructions; "
                "its hash participates in resident-process invalidation."
            )
        except Exception as exc:
            prompt_status = "error"
            prompt_detail = f"Codex instruction bundle could not be measured: {exc}"

    sources: list[dict[str, Any]] = [
        _source(
            "identity_prompt",
            "Identity prompt",
            sent_prompt,
            layer="identity",
            status=prompt_status,
            detail=prompt_detail,
            configured_chars=configured_prompt_chars,
            sha256=(codex_bundle.base_sha256 if codex_bundle else _digest(sent_prompt)),
            source_path=str(identity_prompt_file(identity, ROOT_DIR / "prompts")),
        )
    ]
    if codex_bundle is not None:
        sources.append(_source(
            "codex_anam_contract",
            "Anam Codex operating contract",
            codex_bundle.developer_text,
            layer="developer",
            status="sent",
            detail=(
                "Stable developerInstructions contract; dynamic orientation, mode, "
                "skills, and story state are delivered in the current turn input."
            ),
            sha256=codex_bundle.developer_sha256,
            source_path=str(codex_bundle.developer_path),
            instruction_fingerprint=codex_bundle.fingerprint,
        ))

    claude_md = _read_text(ROOT_DIR / "CLAUDE.md")
    agents_md = _read_text(ROOT_DIR / "AGENTS.md")
    roleplay_packet = build_turn_packet(identity, ROOT_DIR / "prompts")
    if roleplay_packet:
        sources.append(_source(
            "character_turn_packet", "Live roleplay checkpoint and voice anchor",
            roleplay_packet, layer="turn", status="sent",
            detail="Selected package; refreshed from disk on every reply, including mapped ChatGPT threads.",
        ))

    sources.append(_source(
        "repository_claude_md",
        "Repository guidance (CLAUDE.md)",
        claude_md,
        layer="provider",
        status="provider-loaded" if provider == "claude-code" else "inactive",
        detail=(
            "Loaded by Claude Code from the repository working directory."
            if provider == "claude-code"
            else f"Present on disk, but not injected by the {provider} lane."
        ),
    ))
    sources.append(_source(
        "repository_agents_md",
        "Repository guidance (AGENTS.md)",
        agents_md,
        layer="provider",
        status="provider-loaded" if provider == "codex" else "inactive",
        detail=(
            "Loaded natively by Codex from the repository working directory."
            if provider == "codex"
            else f"Present on disk, but not injected by the {provider} lane."
        ),
    ))

    for source_key, label, text in _split_skill_context(skill_context):
        sources.append(_source(source_key, label, text, layer="skills"))
    if provider == "codex" and not any(row["key"] == "skill_catalog" for row in sources):
        sources.append(_source(
            "native_skill_catalog",
            "Native Codex skill catalog",
            None,
            layer="skills",
            status="unmeasured",
            detail="Injected by the Codex runtime outside Anam's payload; its live character count is not exposed here.",
        ))

    if mode_rules:
        sources.append(_source("mode_rules", "Conversation mode rules", mode_rules, layer="turn"))

    pending_key = _key(identity, conversation_id)
    with _LOCK:
        orientation = _PENDING_ORIENTATIONS.get(pending_key)
    if orientation and orientation.get("hash") == _digest(orientation_context):
        for hook in orientation.get("hooks", []):
            sources.append(_source(
                f"orientation.{hook['name']}",
                f"Orientation · {hook['name'].replace('_', ' ')}",
                hook.get("text", ""),
                layer="orientation",
                detail="Cached hook result" if hook.get("cached") else "Built for this turn",
                original_chars=hook.get("original_chars"),
                cap_chars=hook.get("cap_chars"),
            ))
        mode = orientation.get("mode")
        is_warm_turn = bool(orientation.get("is_warm_turn"))
        orientation_matched = True
    else:
        if orientation_context:
            sources.append(_source(
                "orientation.combined",
                "Orientation · combined",
                orientation_context,
                layer="orientation",
                detail="The provider received this block, but its per-hook snapshot was unavailable.",
            ))
        mode = None
        is_warm_turn = None
        orientation_matched = False

    duplicates = _find_duplicates(sources)
    sent_chars = sum(
        int(row["chars"] or 0)
        for row in sources
        if row["status"] in {"sent", "provider-loaded"}
    )
    orientation_chars = sum(
        int(row["chars"] or 0) for row in sources if row["layer"] == "orientation"
    )
    for row in sources:
        row.pop("_text", None)

    now = datetime.now(timezone.utc).isoformat()
    report = {
        "identity": identity,
        "provider": provider,
        "model": model,
        "conversation": (conversation_id or "")[:12],
        "recorded_at": now,
        "mode": mode,
        "warm_turn": is_warm_turn,
        "orientation_snapshot_matched": orientation_matched,
        "known_sent_chars": sent_chars,
        "orientation_chars": orientation_chars,
        "source_count": len(sources),
        "duplicate_count": len(duplicates),
        "sources": sources,
        "duplicates": duplicates,
        "coverage_note": (
            "Counts cover Anam-built context and repository guidance whose provider loading is known. "
            "Provider-owned sources marked unmeasured are shown honestly and excluded from totals."
        ),
    }

    with _LOCK:
        payload = _load_payload()
        payload["version"] = 1
        payload["updated_at"] = now
        payload["identities"][identity_key] = report
        _save_payload(payload)


def get_context_ledgers(identity: str | None = None) -> dict[str, Any]:
    with _LOCK:
        payload = _load_payload()
    records = payload.get("identities", {})
    if identity:
        record = records.get(identity.strip().lower())
        selected = [record] if record else []
    else:
        selected = list(records.values())
    selected = [row for row in selected if isinstance(row, dict)]
    selected.sort(key=lambda row: str(row.get("identity", "")).casefold())
    return {
        "version": payload.get("version", 1),
        "updated_at": payload.get("updated_at"),
        "identities": selected,
    }
