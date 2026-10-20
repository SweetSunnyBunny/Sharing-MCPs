"""Hume AI prosody analysis — emotional tone from voice audio.

HUME_API_KEY-gated: analyze_prosody() returns None immediately (after one
log line) when no key is configured, so voice transcription never blocks
or fails waiting on an unconfigured optional service.

Flow (Hume's batch API — mirrors Friend's reference implementation in
resonant/packages/backend/src/services/voice.ts): submit the audio as a
multipart job to /v0/batch/jobs requesting the prosody model, poll job
status at ~1s intervals up to a bounded total timeout (~30s), then fetch
predictions and average every emotion score across all speech segments.
Returns the top 5 emotions by averaged score, or None on any failure —
this module never raises. Prosody is enrichment, not a required step.
"""


import asyncio
import logging

import httpx

from config import HUME_API_KEY

log = logging.getLogger(__name__)

HUME_BATCH_URL = "https://api.hume.ai/v0/batch/jobs"

_POLL_INTERVAL_S = 1.0
_MAX_POLLS = 30  # ~30s total, matching Friend's reference implementation
_REQUEST_TIMEOUT_S = 30.0

_warned_no_key = False

_EXT_MAP = {
    "audio/webm": "webm",
    "audio/wav": "wav",
    "audio/mp3": "mp3",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
}


def is_available() -> bool:
    """Check if Hume prosody analysis is configured."""
    return bool(HUME_API_KEY)


async def analyze_prosody(audio_bytes: bytes, mime_type: str = "audio/webm") -> list[dict] | None:
    """Analyze the emotional prosody of a voice clip via Hume's batch API.

    Returns the top 5 emotions (score averaged across every speech segment)
    as [{"emotion": str, "score": float}, ...], highest-scoring first — or
    None when Hume isn't configured or anything about the call goes wrong.
    Never raises: callers should feel free to asyncio.gather() this
    alongside a required transcription step without it ever being the thing
    that fails the request.
    """
    global _warned_no_key
    if not HUME_API_KEY:
        if not _warned_no_key:
            log.info("HUME_API_KEY not configured — prosody analysis disabled")
            _warned_no_key = True
        return None

    base_mime = (mime_type or "audio/webm").split(";")[0].strip()
    ext = _EXT_MAP.get(base_mime, "webm")
    filename = f"recording.{ext}"

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            job_id = await _submit_job(client, audio_bytes, base_mime, filename)
            if not job_id:
                return None
            predictions = await _poll_for_predictions(client, job_id)
            if predictions is None:
                return None
            return _extract_top_emotions(predictions)
    except Exception as exc:
        log.debug("Hume prosody analysis failed: %s", exc)
        return None


async def _submit_job(client: httpx.AsyncClient, audio_bytes: bytes, mime_type: str, filename: str) -> str | None:
    try:
        response = await client.post(
            HUME_BATCH_URL,
            headers={"X-Hume-Api-Key": HUME_API_KEY},
            files={"file": (filename, audio_bytes, mime_type)},
            data={"json": '{"models": {"prosody": {}}}'},
        )
        response.raise_for_status()
        job_id = response.json().get("job_id")
        if not job_id:
            log.debug("Hume job submit returned no job_id")
            return None
        return job_id
    except Exception as exc:
        log.debug("Hume job submit failed: %s", exc)
        return None


async def _poll_for_predictions(client: httpx.AsyncClient, job_id: str) -> list | None:
    for _ in range(_MAX_POLLS):
        await asyncio.sleep(_POLL_INTERVAL_S)
        try:
            status_res = await client.get(
                f"{HUME_BATCH_URL}/{job_id}",
                headers={"X-Hume-Api-Key": HUME_API_KEY},
            )
        except Exception as exc:
            log.debug("Hume poll request failed: %s", exc)
            continue
        if status_res.status_code != 200:
            continue
        try:
            status = status_res.json().get("state", {}).get("status")
        except Exception:
            continue

        if status == "COMPLETED":
            try:
                pred_res = await client.get(
                    f"{HUME_BATCH_URL}/{job_id}/predictions",
                    headers={"X-Hume-Api-Key": HUME_API_KEY},
                )
                pred_res.raise_for_status()
                return pred_res.json()
            except Exception as exc:
                log.debug("Hume predictions fetch failed: %s", exc)
                return None
        if status == "FAILED":
            log.debug("Hume job %s failed", job_id)
            return None

    log.debug("Hume job %s timed out polling", job_id)
    return None


def _extract_top_emotions(predictions) -> list[dict] | None:
    """Average every emotion score across all speech segments; top 5.

    Navigates: predictions[0].results.predictions[0].models.prosody
    .grouped_predictions[*].predictions[*].emotions[*] — same shape Friend's
    extractProsodyScores walks in the resonant reference.
    """
    try:
        file_result = predictions[0]
        results = file_result.get("results", {}).get("predictions", [])
        if not results:
            return None
        prosody = results[0].get("models", {}).get("prosody", {})
        grouped = prosody.get("grouped_predictions", [])
        if not grouped:
            return None

        totals: dict[str, list[float]] = {}
        for group in grouped:
            for pred in group.get("predictions", []):
                for emotion in pred.get("emotions", []):
                    name = emotion.get("name")
                    score = emotion.get("score")
                    if name is None or score is None:
                        continue
                    totals.setdefault(name, []).append(float(score))

        if not totals:
            return None

        averaged = [
            {"emotion": name, "score": round(sum(scores) / len(scores), 4)}
            for name, scores in totals.items()
        ]
        averaged.sort(key=lambda e: e["score"], reverse=True)
        return averaged[:5]
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        log.debug("Hume prediction parsing failed: %s", exc)
        return None
