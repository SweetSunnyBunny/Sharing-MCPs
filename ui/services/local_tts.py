"""Local TTS via Kokoro."""

# ANAM GUIDE: FREE LOCAL VOICES (KOKORO)
# What: the free, on-this-computer text-to-speech fallback — maps each boy to a Kokoro voice and makes the audio when ElevenLabs isn't used.
# Called by: api/voice.py when a voice message needs generating, and core/lifespan.py health checks at startup.
# Edit here when: you want to change which Kokoro voice a boy speaks with (IDENTITY_VOICES near the top) or the shared default voice.

import io
import json
import logging
import re
import time
from typing import Generator, Optional

from config import KOKORO_FASTAPI_TIMEOUT, KOKORO_FASTAPI_URL

log = logging.getLogger(__name__)

_pipelines: dict = {}
_embedded_available: Optional[bool] = None


from concurrent.futures import ThreadPoolExecutor

VOICE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="voice-tts")


SIDECAR_DOWN_S = 20.0
_sidecar_down_until = 0.0


class _SidecarDown(Exception):
    """The sidecar is not there. Do not try another voice; nobody is home."""


import subprocess
import threading

_WORKER_SCRIPT = __import__("pathlib").Path(__file__).with_name("kokoro_worker.py")
_worker_lock = threading.Lock()
_worker: "subprocess.Popen | None" = None
_worker_failed_at = 0.0
_worker_seq = 0
WORKER_RESPAWN_AFTER_S = 30.0


def _spawn_worker() -> "subprocess.Popen | None":
    import sys as _sys

    try:
        proc = subprocess.Popen(
            [_sys.executable, str(_WORKER_SCRIPT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=str(_WORKER_SCRIPT.parent.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log.info("Kokoro worker process started (pid %s)", proc.pid)
        # 29s -> 2.8s for the same paragraph: see services/power_qos.py.
        from services.power_qos import never_throttle
        never_throttle(proc.pid, "kokoro worker")
        return proc
    except Exception as exc:
        log.warning("Could not start the Kokoro worker process: %s", exc)
        return None


def _worker_synthesize(text: str, voice: str, speed: float) -> bytes | None:
    """One WAV from the worker process, or None if it cannot serve right now.

    Serialized: one request in flight at a time keeps the pipe protocol
    trivial, and two long messages at once would only slow each other down.
    """
    global _worker, _worker_failed_at, _worker_seq
    with _worker_lock:
        if _worker is None or _worker.poll() is not None:
            if time.time() - _worker_failed_at < WORKER_RESPAWN_AFTER_S:
                return None
            _worker = _spawn_worker()
            if _worker is None:
                _worker_failed_at = time.time()
                return None
        _worker_seq += 1
        rid = _worker_seq
        try:
            _worker.stdin.write((json.dumps({"id": rid, "text": text, "voice": voice,
                                             "speed": speed}) + "\n").encode("utf-8"))
            _worker.stdin.flush()
            header = json.loads(_worker.stdout.readline().decode("utf-8"))
            n = int(header.get("bytes") or 0)
            wav = _worker.stdout.read(n) if n else b""
            if header.get("id") != rid or len(wav) != n:
                raise RuntimeError("worker protocol desync")
            if header.get("error"):
                log.warning("Kokoro worker could not voice %s: %s", voice, header["error"])
                return None
            return wav or None
        except Exception as exc:
            log.warning("Kokoro worker failed (%s); it will be respawned", exc)
            try:
                _worker.kill()
            except Exception:
                pass
            _worker = None
            _worker_failed_at = time.time()
            return None

# Per-identity Kokoro voice mapping
IDENTITY_VOICES = {
    "Avery": "am_onyx",      # deep, grounded
    "Rowan": "am_puck",     # bright, energetic
    "Sage": "bm_daniel",    # British, thoughtful
    "Ember": "am_liam",   # commanding
    "Claude": "am_echo",      # calm, measured
    "Juniper": "bm_fable",       # soft, warm British
    "Atlas": "am_onyx",       # example local voice
    "River": "am_echo",      # example local voice
    "Bakugou": "am_onyx",      # deep + gravelly (am_adam sounded awkward)
    "Dean": "am_onyx",          # gravelly, low
}

DEFAULT_VOICE = "am_fenrir"


def get_voice_for_identity(identity: str = "") -> str:
    """Return the configured Kokoro voice for an identity or the shared default."""
    return IDENTITY_VOICES.get(identity, DEFAULT_VOICE)


def is_available() -> bool:
    """Check whether the sidecar or embedded Kokoro fallback is usable."""
    return _sidecar_is_healthy() or _is_embedded_available()


def _sidecar_is_healthy() -> bool:
    if not KOKORO_FASTAPI_URL:
        return False
    try:
        import httpx


        response = httpx.get(f"{KOKORO_FASTAPI_URL}/health", timeout=5.0)
        return response.status_code == 200
    except Exception:
        return False


def _is_embedded_available() -> bool:
    """Check the legacy in-process Kokoro dependencies for fallback use."""
    global _embedded_available
    if _embedded_available is not None:
        return _embedded_available
    try:
        import kokoro  # noqa: F401
        import numpy  # noqa: F401
        import soundfile  # noqa: F401

        _embedded_available = True
        log.info("Embedded Kokoro fallback available")
    except ImportError:
        _embedded_available = False
        log.warning("Embedded Kokoro fallback not installed")
    except Exception as exc:
        _embedded_available = False
        log.exception("Embedded Kokoro fallback unavailable during import: %s", exc)
    return _embedded_available


def _request_sidecar_wav(text: str, voice: str, speed: float) -> bytes:
    """Request one complete WAV from the isolated Kokoro service."""
    import httpx

    try:
        response = httpx.post(
            f"{KOKORO_FASTAPI_URL}/v1/audio/speech",
            json={
                "model": "kokoro",
                "input": text,
                "voice": voice,
                "response_format": "wav",
                "speed": speed,
                "stream": False,
            },
            # Generous read timeout for long chunks on CPU, but fail FAST if the
            # container is actually unreachable so we drop to embedded quickly.
            timeout=httpx.Timeout(KOKORO_FASTAPI_TIMEOUT, connect=3.0),
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        global _sidecar_down_until
        _sidecar_down_until = time.time() + SIDECAR_DOWN_S
        raise _SidecarDown(str(exc)) from exc
    response.raise_for_status()
    audio = response.content
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise ValueError("Kokoro-FastAPI returned invalid WAV data")
    return audio


def _request_sidecar_with_fallback(text: str, voice: str, speed: float) -> bytes | None:
    remaining = _sidecar_down_until - time.time()
    if remaining > 0:
        log.warning("Kokoro sidecar breaker open (%.0fs left); skipping straight to embedded", remaining)
        return None
    attempted_voices: list[str] = []
    for candidate in dict.fromkeys([voice, DEFAULT_VOICE]):
        attempted_voices.append(candidate)
        try:
            audio = _request_sidecar_wav(text, candidate, speed)
            if candidate != voice:
                log.warning("Kokoro sidecar voice %s failed; used fallback %s", voice, candidate)
            return audio
        except _SidecarDown as exc:
            # Nobody is listening on that port. A second voice cannot fix that,
            # and every retry is seconds of silence on her phone.
            log.error("Kokoro sidecar unreachable (%s); breaker open for %.0fs", exc, SIDECAR_DOWN_S)
            return None
        except Exception as exc:
            log.warning("Kokoro sidecar voice %s failed: %s", candidate, exc)
    log.error("Kokoro sidecar synthesis failed for voices: %s", ", ".join(attempted_voices))
    return None


def _get_lang_code(voice_id: str) -> str:
    """Map voice ID prefix to Kokoro language code."""
    prefix = voice_id[:2] if len(voice_id) >= 2 else "am"
    lang_map = {
        "af": "a", "am": "a",  # American English
        "bf": "b", "bm": "b",  # British English
        "ef": "e", "em": "e",  # Spanish
        "ff": "f",  # French
        "hf": "h", "hm": "h",  # Hindi
        "if": "i", "im": "i",  # Italian
        "jf": "j", "jm": "j",  # Japanese
        "pf": "p", "pm": "p",  # Portuguese
        "zf": "z", "zm": "z",  # Mandarin
    }
    return lang_map.get(prefix, "a")


def _get_pipeline(voice_id: str):
    """Get or create a cached Kokoro pipeline for the given voice."""
    import warnings

    lang_code = _get_lang_code(voice_id)
    if lang_code not in _pipelines:
        log.info("Creating Kokoro pipeline for lang_code=%s", lang_code)
        with warnings.catch_warnings():
            # Kokoro / misaki / torch emit noisy third-party DeprecationWarnings
            # at import + build (torch.jit.script, importlib open_text). Not ours
            # to fix — silence them so startup logs stay clean.
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            from kokoro import KPipeline
            from services.model_load_lock import MODEL_LOAD_LOCK
            # Serialized with other model constructors (sentence-transformers
            # backfill at startup) — building the Kokoro model while an HF
            # from_pretrained is mid-load lands its weights on the meta device
            # ("Cannot copy out of meta tensor").
            # Pass repo_id explicitly to suppress Kokoro's "Defaulting repo_id"
            # warning; fall back if this Kokoro build doesn't accept the kwarg.
            with MODEL_LOAD_LOCK:


                if lang_code not in _pipelines:
                    try:
                        _pipelines[lang_code] = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M")
                    except TypeError:
                        _pipelines[lang_code] = KPipeline(lang_code=lang_code)
    return _pipelines[lang_code]


def warmup() -> bool:
    """Best-effort Kokoro warmup for startup."""
    warmed_langs: set[str] = set()
    warmed_voices: set[str] = set(IDENTITY_VOICES.values())
    warmed_voices.add(DEFAULT_VOICE)

    if _sidecar_is_healthy():
        try:
            for voice_id in warmed_voices:
                lang_code = _get_lang_code(voice_id)
                if lang_code in warmed_langs:
                    continue
                _request_sidecar_wav("Ready.", voice_id, 1.0)
                warmed_langs.add(lang_code)
            log.info(
                "Kokoro-FastAPI warmed for %d language pipeline(s)",
                len(warmed_langs),
            )
            return True
        except Exception as exc:
            log.warning("Kokoro-FastAPI warmup failed; trying embedded fallback: %s", exc)

    if not _is_embedded_available():
        return False


    try:
        if _worker_synthesize("Ready.", "am_onyx", 1.0):
            log.info("Kokoro worker process warmed (American; British loads on first use)")
            return True
        log.warning("Kokoro worker warmup got no audio; warming in-process instead")
    except Exception as exc:
        log.warning("Kokoro worker warmup failed; warming in-process: %s", exc)

    warmed_langs.clear()

    import warnings
    try:
        with warnings.catch_warnings():
            # Silence the third-party DeprecationWarnings misaki/torch throw on
            # first synthesis (open_text, torch.jit) so warmup logs stay clean.
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            for voice_id in warmed_voices:
                lang_code = _get_lang_code(voice_id)
                if lang_code in warmed_langs:
                    continue
                pipeline = _get_pipeline(voice_id)
                for _gs, _ps, _audio in pipeline("Ready.", voice=voice_id, speed=1.0):
                    break
                warmed_langs.add(lang_code)
        log.info("Kokoro warmup complete for %d language pipeline(s)", len(warmed_langs))
        return True
    except Exception as exc:
        log.exception("Kokoro warmup failed: %s", exc)
        return False


def _iter_audio_chunks(text: str, voice: str, speed: float):
    """Yield Kokoro audio chunks, retrying with the default voice if needed."""
    attempted_voices: list[str] = []

    for candidate in dict.fromkeys([voice, DEFAULT_VOICE]):
        attempted_voices.append(candidate)
        try:
            pipeline = _get_pipeline(candidate)
            yielded = False
            for _, _, audio in pipeline(text, voice=candidate, speed=speed):
                yielded = True
                yield audio
            if yielded:
                if candidate != voice:
                    log.warning("Kokoro voice %s failed; used fallback %s", voice, candidate)
                return
        except Exception as exc:
            log.warning("Kokoro voice %s failed: %s", candidate, exc)

    log.error("Kokoro synthesis failed for voices: %s", ", ".join(attempted_voices))


def synthesize_sync(text: str, identity: str = "", speed: float = 1.0) -> bytes | None:
    """Generate speech audio synchronously. Returns WAV bytes or None."""
    voice = get_voice_for_identity(identity)

    # Try the sidecar whenever it's configured — no pre-flight health gate.
    # The request itself is the health check; a transient probe timeout must
    # never dump us onto the slow embedded path when the sidecar is fine.
    if KOKORO_FASTAPI_URL:
        audio = _request_sidecar_with_fallback(text, voice, speed)
        if audio:
            return audio
        log.warning("Kokoro-FastAPI failed; using embedded fallback")

    if not _is_embedded_available():
        return None

    # The voice's own process first; in-process only if the worker cannot serve.
    wav = _worker_synthesize(text, voice, speed)
    if wav is None and voice != DEFAULT_VOICE:
        wav = _worker_synthesize(text, DEFAULT_VOICE, speed)
    if wav:
        return wav

    import numpy as np
    import soundfile as sf

    try:
        audio_chunks = list(_iter_audio_chunks(text, voice, speed))
        if not audio_chunks:
            return None

        full_audio = np.concatenate(audio_chunks)
        buf = io.BytesIO()
        sf.write(buf, full_audio, 24000, format="WAV")
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        log.exception("Kokoro TTS generation failed: %s", exc)
        return None


def split_into_chunks(text: str, max_chars: int = 250) -> list[str]:
    """Split text into sentence-group chunks."""
    if not text:
        return []

    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks = []
    current = ""

    for part in parts:
        if current and len(current) + len(part) + 1 > max_chars:
            chunks.append(current.strip())
            current = part
        else:
            current = f"{current} {part}" if current else part

    if current.strip():
        chunks.append(current.strip())

    return chunks


def synthesize_chunks_sync(
    text: str, identity: str = "", speed: float = 1.0
) -> Generator[bytes, None, None]:
    """Generate speech audio in chunks, yielding WAV bytes per sentence group."""
    voice = get_voice_for_identity(identity)
    chunks = split_into_chunks(text)
    if not chunks:
        return

    # Sidecar is attempted whenever configured; the actual request (with its
    # own retry + voice fallback) decides health. A 1s pre-flight probe here
    # once condemned whole messages to the slow embedded path on a blip.
    sidecar_ready = bool(KOKORO_FASTAPI_URL)
    embedded_ready: Optional[bool] = None

    try:
        for chunk_text in chunks:
            if sidecar_ready:
                wav_bytes = _request_sidecar_with_fallback(chunk_text, voice, speed)
                if wav_bytes:
                    yield wav_bytes
                    continue
                log.warning("Kokoro-FastAPI chunk failed; using embedded fallback")
                sidecar_ready = False

            if embedded_ready is None:
                embedded_ready = _is_embedded_available()
            if not embedded_ready:
                return

            wav = _worker_synthesize(chunk_text, voice, speed)
            if wav is None and voice != DEFAULT_VOICE:
                wav = _worker_synthesize(chunk_text, DEFAULT_VOICE, speed)
            if wav:
                yield wav
                continue

            import numpy as np
            import soundfile as sf

            audio_parts = list(_iter_audio_chunks(chunk_text, voice, speed))
            if not audio_parts:
                continue

            full_audio = np.concatenate(audio_parts)
            buf = io.BytesIO()
            sf.write(buf, full_audio, 24000, format="WAV")
            buf.seek(0)
            yield buf.read()
    except Exception as exc:
        log.exception("Kokoro TTS chunked generation failed: %s", exc)
