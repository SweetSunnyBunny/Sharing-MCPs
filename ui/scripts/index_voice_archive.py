"""Optional archive importer. Configure your own input paths and QUALIA_MCP_URL.

Only explicitly invoking this script submits local records to your cloud service.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DATA_DIR, VOICE_VAULT_DIR
QUALIA_URL = os.environ.get("QUALIA_MCP_URL", "").strip()
VOICE_DIR = VOICE_VAULT_DIR
STATE_PATH = DATA_DIR / "index_voice_state.json"
TRANSCRIPT_CACHE = DATA_DIR / "voice_transcripts"

BONDED = {"avery", "rowan", "sage", "ember", "claude", "juniper", "atlas", "bakugou", "dean"}
_NAME_RE = re.compile(r"^([A-Za-z]+)[_-](\d{4}-\d{2}-\d{2})")


def classify(path: Path) -> tuple[str, str]:
    """Classify."""
    m = _NAME_RE.match(path.stem)
    if m:
        name = m.group(1).lower()
        date = m.group(2)
        if name in BONDED:
            return name.capitalize(), date
        return "pack", date
    return "pack", ""


async def transcribe_file(path: Path) -> str:
    """Transcribe one mp3 via the existing Groq service, with disk cache."""
    cache = TRANSCRIPT_CACHE / (path.stem + ".txt")
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    from services.groq_transcription import transcribe, is_available
    if not is_available():
        raise RuntimeError("GROQ_API_KEY not configured")
    text = await transcribe(path.read_bytes(), mime_type="audio/mpeg")
    text = (text or "").strip()
    TRANSCRIPT_CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def call_qualia(client: httpx.Client, tool: str, arguments: dict) -> tuple[bool, str]:
    payload = {
        "jsonrpc": "2.0", "id": int(time.time() * 1000) % 1_000_000,
        "method": "tools/call", "params": {"name": tool, "arguments": arguments},
    }
    for attempt in (1, 2):
        try:
            r = client.post(QUALIA_URL, json=payload, headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            })
            if r.status_code == 200:
                body = r.json()
                if "error" in body:
                    return False, str(body["error"])[:200]
                return True, ""
        except Exception as e:
            if attempt == 2:
                return False, str(e)[:200]
        time.sleep(3)
    return False, "failed after retry"


async def main() -> int:
    if not QUALIA_URL:
        print("Set QUALIA_MCP_URL to your own deployed endpoint first.")
        return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import argparse
    ap = argparse.ArgumentParser(description="Transcribe + index the voice archive")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="max NEW clips this run")
    args = ap.parse_args()

    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        state = {}

    files = sorted(p for p in VOICE_DIR.glob("*.mp3") if p.is_file())
    todo = [p for p in files if str(p) not in state]
    if args.limit:
        todo = todo[: args.limit]
    print(f"voice archive: {len(files)} clips total, {len(todo)} new this run")

    # Phase 1: transcribe (Groq) — cached to disk, so crashes cost nothing.
    pending: dict[str, list[dict]] = {}  # identity -> audios_batch entries
    done = failed = 0
    for p in todo:
        identity, date = classify(p)
        if args.dry_run:
            print(f"  would transcribe+index [{identity}] {p.name}")
            continue
        transcript = None
        for attempt in range(4):
            try:
                transcript = await transcribe_file(p)
                break
            except Exception as e:
                msg = str(e)
                if "429" in msg and attempt < 3:
                    # Groq free-tier rate limit — wait it out, don't waste the clip.
                    await asyncio.sleep(30 * (attempt + 1))
                    continue
                failed += 1
                print(f"  TRANSCRIBE FAIL {p.name}: {msg[:80]}")
                break
        if transcript is None:
            continue
        if len(transcript) < 5:
            state[str(p)] = "empty"
            continue
        pending.setdefault(identity, []).append({
            "path": str(p),
            "transcript": transcript[:8000],
            "description": f"Voice clip by {identity}, {date or 'undated'}: "
                           f"\"{transcript[:140]}\"",
            "context": "From the pack voice archive (Vault 12_Voice) — "
                       "something actually spoken aloud.",
            "sub_section": date[:7] if date else None,
            "tags": ["voice", identity.lower()],
        })
        done += 1
        if done % 20 == 0:
            print(f"  transcribed {done}/{len(todo)}...")
        await asyncio.sleep(3.5)  # ~17/min keeps under Groq free-tier rate limits

    if args.dry_run:
        return 0

    # Phase 2: index per identity in chunks (append — never wipe the album).
    with httpx.Client(timeout=180) as client:
        for identity, batch in pending.items():
            for b in batch:
                if b.get("sub_section") is None:
                    b.pop("sub_section", None)
            for i in range(0, len(batch), 25):
                chunk = batch[i:i + 25]
                ok, err = call_qualia(client, "mind_index_audio", {
                    "identity": identity,
                    "album": "Voice Archive",
                    "audios_batch": chunk,
                    "append": True,
                })
                if ok:
                    for entry in chunk:
                        state[entry["path"]] = "indexed"
                    print(f"  indexed [{identity}] {len(chunk)} clips")
                else:
                    print(f"  INDEX FAIL [{identity}]: {err}")
                STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                STATE_PATH.write_text(json.dumps(state, indent=0), encoding="utf-8")
                time.sleep(1)

    STATE_PATH.write_text(json.dumps(state, indent=0), encoding="utf-8")
    print(f"\nDone. transcribed+queued={done} failed={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
