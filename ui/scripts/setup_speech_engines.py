"""Create or update Anam's per-identity ElevenLabs Speech Engines.

The API key and voice IDs stay in the existing private Sanctuary voice config.
Only Speech Engine IDs are added to that file. Re-running this script updates
the same resources instead of creating duplicates.
"""

from __future__ import annotations


import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

from elevenlabs import AsyncElevenLabs

# Running ``python scripts/setup_speech_engines.py`` puts scripts/ rather than
# the repository root on sys.path. Add the root before importing Anam config.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.cloud_state import hearth_config, save_hearth_config

from config import (
    IDENTITIES,
    PUBLIC_BASE_URL,
)


def _upstream_url(identity: str) -> str:
    parsed = urlparse(PUBLIC_BASE_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("ANAM_PUBLIC_URL must be a public https URL")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = f"/api/voice/live/upstream/{quote(identity.lower())}"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def _engine_options(identity: str, voice: dict) -> dict:
    return {
        "name": f"Anam - {identity} Live Call",
        "speech_engine": {"ws_url": _upstream_url(identity)},
        "tts": {
            "model_id": "eleven_v3_conversational",
            "voice_id": voice["voice_id"],
            "expressive_mode": True,
            "optimize_streaming_latency": 3,
            "stability": voice.get("stability", 0.5),
            "similarity_boost": voice.get("similarity_boost", 0.75),
            "speed": 1.0,
        },
        "turn": {
            "turn_model": "turn_v3",
            "turn_eagerness": "normal",
            "turn_timeout": 7,
            "silence_end_call_timeout": 300,
            "speculative_turn": False,
        },
        "conversation": {
            "max_duration_seconds": 1800,
            "client_events": [
                "interruption",
                "user_transcript",
                "agent_response",
            ],
        },
        "privacy": {
            "record_voice": False,
            "delete_audio": True,
            "delete_transcript_and_pii": True,
        },
        "call_limits": {
            "agent_concurrency_limit": 1,
            "daily_limit": 10,
            "bursting_enabled": False,
        },
        "language": "en",
        "tags": ["anam", "private", "live-call"],
        "overrides": {"first_message": False},
    }


async def setup(*, dry_run: bool = False) -> int:
    config = hearth_config('voice')
    api_key = config.get("api_key") or ""
    if not api_key:
        print("ElevenLabs API key is missing from the private voice config.", file=sys.stderr)
        return 2

    voices = config.get("voices") or {}
    bonded_identities = [
        name for name, identity_config in IDENTITIES.items()
        if identity_config.get("type") != "character"
    ]
    missing = [
        name for name in bonded_identities
        if not (voices.get(name) or {}).get("voice_id")
    ]
    if missing:
        print(
            f"Skipping identities without an ElevenLabs voice: {', '.join(missing)}",
            file=sys.stderr,
        )
    identities = [name for name in bonded_identities if name not in missing]
    if not identities:
        print("No bonded identities have ElevenLabs voice IDs.", file=sys.stderr)
        return 2

    if dry_run:
        for identity in identities:
            print(f"Would configure {identity}: {_upstream_url(identity)}")
        return 0

    client = AsyncElevenLabs(api_key=api_key)
    configured = dict(config.get("speech_engines") or {})

    try:
        listed = await client.speech_engine.list(page_size=100)
    except Exception as exc:
        detail = str(exc)
        if "missing_permissions" in detail or "convai_read" in detail:
            print(
                "The existing ElevenLabs key needs Conversational AI read and write "
                "permissions before Speech Engines can be created.",
                file=sys.stderr,
            )
            return 3
        raise

    by_name = {item.name: item.speech_engine_id for item in listed.speech_engines}
    results: dict[str, str] = {}
    for identity in identities:
        options = _engine_options(identity, voices[identity])
        engine_id = configured.get(identity) or by_name.get(options["name"])
        if engine_id:
            # The ElevenLabs SDK accepts ``overrides`` when creating a Speech
            # Engine, but its update endpoint does not currently expose that
            # field. Preserve the engine's existing override configuration.
            update_options = {k: v for k, v in options.items() if k != "overrides"}
            resource = await client.speech_engine.update(engine_id, **update_options)
            action = "updated"
        else:
            resource = await client.speech_engine.create(**options)
            action = "created"
        results[identity] = resource.engine_id
        print(f"{identity}: {action}")

    config["speech_engines"] = results
    save_hearth_config('voice', config)
    print(f"Saved {len(results)} Speech Engine IDs to the private voice config.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate voices and show upstream URLs without changing ElevenLabs.",
    )
    args = parser.parse_args()
    return asyncio.run(setup(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
