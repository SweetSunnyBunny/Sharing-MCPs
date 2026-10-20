"""Reusable test voice engine setting support."""

import unittest

from fastapi.responses import JSONResponse

from api import settings
from db.database import get_db, release_db
from db.schema import init_db
from services import voice_call


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — only .json() is used here."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _VoiceEngineCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db = await get_db()
        try:
            await init_db(db)
        finally:
            await release_db(db)

    async def asyncTearDown(self):
        # Leave the switch where it started so test order can't matter.
        await settings._set_setting(settings._VOICE_ENGINE_KEY, "kokoro")

    async def _set(self, engine):
        return await settings.set_voice_engine(_FakeRequest({"engine": engine}))

    async def _get(self):
        return (await settings.get_voice_engine())["engine"]


class VoiceEngineSettingTests(_VoiceEngineCase):
    """GET/PUT round-trip against the throwaway test DB (see conftest)."""

    async def test_default_is_kokoro_when_never_set(self):
        await settings._set_setting(settings._VOICE_ENGINE_KEY, "")
        body = await settings.get_voice_engine()
        self.assertEqual(body["engine"], "kokoro")
        self.assertEqual(body["available"], ["kokoro", "elevenlabs"])

    async def test_put_then_get_round_trips_elevenlabs(self):
        result = await self._set("elevenlabs")
        self.assertEqual(result, {"ok": True, "engine": "elevenlabs"})
        self.assertEqual(await self._get(), "elevenlabs")

    async def test_switching_back_switches_back(self):

        await self._set("elevenlabs")
        await self._set("kokoro")
        self.assertEqual(await self._get(), "kokoro")

    async def test_case_and_whitespace_are_forgiven(self):
        await self._set("  ElevenLabs  ")
        self.assertEqual(await self._get(), "elevenlabs")

    async def test_unknown_engine_is_rejected_and_leaves_the_setting_alone(self):
        await self._set("elevenlabs")
        response = await self._set("squeaky")
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(await self._get(), "elevenlabs")

    async def test_garbage_stored_out_of_band_still_reads_as_kokoro(self):
        # A hand-edited DB row must never leave a call with no engine at all.
        await settings._set_setting(settings._VOICE_ENGINE_KEY, "not-an-engine")
        self.assertEqual(await self._get(), "kokoro")


class CallHonorsTheSwitchTests(_VoiceEngineCase):
    """The wake call reads the same key the Settings page writes."""

    async def test_same_key_both_sides(self):
        # If these ever drift apart, the switch silently stops working — the
        # exact failure shape she hit. Pin them together.
        self.assertEqual(settings._VOICE_ENGINE_KEY, "voice_tts_engine")

    async def test_call_default_follows_the_setting_live(self):
        await self._set("elevenlabs")
        self.assertEqual(await voice_call.default_tts_engine(), "elevenlabs")
        await self._set("kokoro")
        self.assertEqual(await voice_call.default_tts_engine(), "kokoro")

    async def test_call_default_is_kokoro_when_unset(self):
        await settings._set_setting(settings._VOICE_ENGINE_KEY, "")
        self.assertEqual(await voice_call.default_tts_engine(), "kokoro")

    async def test_hello_precedence_explicit_beats_setting_blank_falls_through(self):
        """Mirrors the resolution line in serve_voice_call's hello handling.

        An explicit tts in the hello is a deliberate per-call override (the
        test page's dropdown). Blank/missing means "use my Anam setting" —
        which is what the AnamCompanion ear and the default option send.
        """
        await self._set("elevenlabs")

        async def resolve(hello):
            requested = str(hello.get("tts") or "").strip().lower()
            if requested in voice_call._TTS_ENGINES:
                return requested
            return await voice_call.default_tts_engine()

        self.assertEqual(await resolve({"tts": "kokoro"}), "kokoro")      # override
        self.assertEqual(await resolve({"tts": ""}), "elevenlabs")        # default opt
        self.assertEqual(await resolve({}), "elevenlabs")                 # phone ear
        self.assertEqual(await resolve({"tts": "banjo"}), "elevenlabs")   # junk


if __name__ == "__main__":
    unittest.main()
