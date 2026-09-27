import json
import unittest
from pathlib import Path
from unittest.mock import patch

from config import IDENTITIES
from services.cloud_state import hearth_config
from services import local_tts


# The bonded identities must each have an explicit voice. Character masks
# (type="character": Bakugou, Dean, Doctor, Sans, ...) may be wired
# individually but otherwise fall back to the default voice by design —
# see test_kokoro_falls_back_to_default_voice.
BONDED_IDENTITIES = [
    name for name, cfg in IDENTITIES.items()
    if cfg.get("type") != "character"
]


class VoiceWiringTests(unittest.TestCase):
    def test_kokoro_mapping_covers_all_identities(self):
        missing = sorted(set(BONDED_IDENTITIES) - set(local_tts.IDENTITY_VOICES))
        self.assertEqual(missing, [])
        for identity in BONDED_IDENTITIES:
            voice = local_tts.get_voice_for_identity(identity)
            self.assertIsInstance(voice, str)
            self.assertTrue(voice)

    def test_elevenlabs_config_covers_all_identities(self):
        cfg = hearth_config('voice')
        voices = cfg.get("voices", {})
        missing = sorted(set(BONDED_IDENTITIES) - set(voices))
        self.assertEqual(missing, [])
        for identity in BONDED_IDENTITIES:
            self.assertTrue(voices[identity].get("voice_id"), identity)

    def test_kokoro_falls_back_to_default_voice(self):
        calls = []

        def fake_get_pipeline(voice_id):
            def run(text, *, voice, speed):
                calls.append((voice_id, voice, text, speed))
                if voice_id == "am_puck":
                    raise RuntimeError("missing voice")
                yield "", "", b"audio"

            return run

        with patch.object(local_tts, "_get_pipeline", side_effect=fake_get_pipeline):
            chunks = list(local_tts._iter_audio_chunks("hello there", "am_puck", 1.0))

        self.assertEqual(chunks, [b"audio"])
        self.assertEqual(calls[0][0], "am_puck")
        self.assertEqual(calls[1][0], local_tts.DEFAULT_VOICE)

    def test_split_into_chunks_preserves_full_text_across_multiple_chunks(self):
        text = (
            "This is the first sentence. "
            "This is the second sentence and it is intentionally a bit longer. "
            "This is the third sentence so the chunker has to keep going. "
            "This is the fourth sentence to force multiple streamed chunks."
        )

        chunks = local_tts.split_into_chunks(text, max_chars=90)

        self.assertGreater(len(chunks), 1)
        self.assertEqual(" ".join(chunks), text)

    def test_sidecar_voice_falls_back_to_default_voice(self):
        calls = []

        def fake_request(text, voice, speed):
            calls.append((text, voice, speed))
            if voice == "am_puck":
                raise RuntimeError("voice unavailable")
            return b"RIFF....WAVE"

        with patch.object(local_tts, "_request_sidecar_wav", side_effect=fake_request):
            audio = local_tts._request_sidecar_with_fallback("hello", "am_puck", 1.0)

        self.assertEqual(audio, b"RIFF....WAVE")
        self.assertEqual([call[1] for call in calls], ["am_puck", local_tts.DEFAULT_VOICE])

    def test_synthesize_prefers_sidecar_without_loading_embedded_kokoro(self):
        # Pin the sidecar URL: this asserts the routing contract that holds
        # WHEN a sidecar is configured, and must not depend on whether this
        # machine currently has one enabled in .env.
        with patch.object(
            local_tts, "KOKORO_FASTAPI_URL", "http://127.0.0.1:8880"
        ), patch.object(local_tts, "_sidecar_is_healthy", return_value=True), patch.object(
            local_tts, "_request_sidecar_with_fallback", return_value=b"RIFF....WAVE"
        ), patch.object(local_tts, "_is_embedded_available") as embedded:
            audio = local_tts.synthesize_sync("hello", "Rowan")

        self.assertEqual(audio, b"RIFF....WAVE")
        embedded.assert_not_called()


if __name__ == "__main__":
    unittest.main()
