"""ElevenLabs API client for text-to-speech."""

# ANAM GUIDE: ELEVENLABS VOICE MAKER
# What: Talks to ElevenLabs to turn a boy's text into real spoken audio, using each identity's own voice from the voice config file (and handling expression tags like [softly]).
# Called by: api/voice.py (voice playback routes), services/chat_turn_finalize.py (<voice> tags in replies), and services/speech_engine_live.py (live calls).
# Edit here when: A boy's voice sounds wrong (settings/model), you want to allow or strip different [expression] tags, or ElevenLabs changes their API. To change WHICH voice a boy uses, edit the voice config JSON (path in config.py), not this file.

import json
import logging
import re

import httpx

from services.cloud_state import hearth_config

log = logging.getLogger(__name__)

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"

_config = None
_client: httpx.AsyncClient | None = None


def _load_config() -> dict:
    return hearth_config('voice')


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            http2=True,
        )
    return _client


_V3_EXPRESSION_TAGS = re.compile(
    r'\[(laughs?|sighs?|softly|whispers?|gasps?|grins?|chuckles?|happily|sadly'
    r'|angrily|excitedly|nervously|sarcastically|tenderly|breathlessly'
    r'|gently|firmly|quietly|warmly|playfully|growls?)\]',
    re.IGNORECASE,
)

_declared_tags: frozenset[str] | None = None


def _declared_expression_tags() -> frozenset[str]:
    """Every bracketed tag declared in any identity's expression_guide.

    Owner authors these per-boy in voice_config.json (preferred_tags,
    emotional_range, wolf_sounds, example). They are, by definition, the
    intended vocabulary — so they are preserved rather than stripped.
    Union across identities: a cross-boy tag is harmless and rare.
    """
    global _declared_tags
    if _declared_tags is not None:
        return _declared_tags

    found: set[str] = set()

    def _walk(node) -> None:
        if isinstance(node, str):
            found.update(t.lower() for t in re.findall(r'\[[^\[\]]{1,48}\]', node))
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                _walk(v)

    try:
        for voice_cfg in (_load_config().get("voices") or {}).values():
            if isinstance(voice_cfg, dict):
                _walk(voice_cfg.get("expression_guide"))
    except Exception:
        log.warning("Could not read expression guides; using base tag list", exc_info=True)

    _declared_tags = frozenset(found)
    return _declared_tags


def _strip_expression_tags(text: str, *, keep_v3: bool = False) -> str:
    """Remove expression tags like [growls], [softly], etc. before sending to TTS.

    When *keep_v3* is True, recognised ElevenLabs v3 expression tags — and every
    tag declared in the pack's own voice guides — are preserved so the voice
    model can render them expressively.
    """
    if keep_v3:
        declared = _declared_expression_tags()

        def _replace(m: re.Match) -> str:
            tag = m.group(0)
            if _V3_EXPRESSION_TAGS.fullmatch(tag) or tag.lower() in declared:
                return tag
            return ""

        return re.sub(r'\[.*?\]', _replace, text).strip()
    return re.sub(r'\[.*?\]', '', text).strip()


def _clean_for_speech(text: str) -> str:
    """Clean markdown and formatting from text for natural speech."""
    # Remove markdown bold/italic
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # Remove markdown headers
    text = re.sub(r'^#{1,4}\s+', '', text, flags=re.MULTILINE)
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Remove inline code
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Remove links [text](url)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Remove non-v3 expression tags (v3 tags preserved for expressive rendering)
    text = _strip_expression_tags(text, keep_v3=True)
    # Collapse whitespace
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


async def synthesize(
    identity: str,
    text: str,
    *,
    model_override: str | None = None,
    output_format_override: str | None = None,
) -> bytes | None:
    """Generate speech audio for the given identity and text.

    Returns MP3 audio bytes, or None on failure.
    """
    config = _load_config()
    api_key = config.get("api_key", "")
    model = model_override or config.get("model", "eleven_v3")
    output_format = output_format_override or config.get("output_format", "mp3_44100_128")
    voices = config.get("voices", {})
    voice_cfg = voices.get(identity) or next(
        (v for k, v in voices.items() if k.lower() == identity.lower()),
        None,
    )

    if not voice_cfg:
        log.warning("No voice config for identity: %s", identity)
        return None

    if not api_key:
        log.warning("No ElevenLabs API key configured")
        return None

    voice_id = voice_cfg["voice_id"]
    clean_text = _clean_for_speech(text)

    if not clean_text:
        return None

    # Truncate very long text (ElevenLabs has limits)
    if len(clean_text) > 5000:
        clean_text = clean_text[:5000] + "..."

    url = f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}"

    payload = {
        "text": clean_text,
        "model_id": model,
        "output_format": output_format,
        "enable_logging": True,
        "voice_settings": {
            "stability": voice_cfg.get("stability", 0.5),
            "similarity_boost": voice_cfg.get("similarity_boost", 0.75),
            "style": voice_cfg.get("style", 0.0),
            "use_speaker_boost": voice_cfg.get("use_speaker_boost", True),
        },
    }

    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    try:
        client = _get_client()
        resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code == 200:
            log.info("TTS generated for %s (%d bytes)", identity, len(resp.content))
            return resp.content

        log.error("ElevenLabs error %d: %s", resp.status_code, resp.text[:200])
        return None

    except Exception as e:
        log.exception("ElevenLabs TTS request failed: %s", e)
        return None
