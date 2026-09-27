"""The Ear's rearm signal — GET /api/voice/call/active."""

import asyncio
import unittest

from services import voice_call


class CallPresenceRegistryTests(unittest.TestCase):
    def setUp(self):
        voice_call._ACTIVE_CALLS.clear()

    tearDown = setUp

    def test_starts_empty(self):
        self.assertEqual(voice_call.active_calls(), [])

    def test_registers_and_clears(self):
        token = voice_call.call_started("Avery")
        self.assertEqual(voice_call.active_calls(), ["Avery"])
        voice_call.call_ended(token)
        self.assertEqual(voice_call.active_calls(), [])

    def test_overlapping_calls_for_one_identity(self):
        """Two sockets for the same boy: the first ending must NOT free him.

        Otherwise the Ear rearms mid-conversation and steals her mic.
        """
        first = voice_call.call_started("Avery")
        second = voice_call.call_started("Avery")
        voice_call.call_ended(first)
        self.assertEqual(voice_call.active_calls(), ["Avery"])
        voice_call.call_ended(second)
        self.assertEqual(voice_call.active_calls(), [])

    def test_unknown_token_is_harmless(self):
        voice_call.call_ended(9_999_999)
        self.assertEqual(voice_call.active_calls(), [])

    def test_stale_registration_expires(self):
        """A leaked registration must not leave her deaf forever.

        If a crash skips call_ended(), the entry ages out and the Ear starts
        listening again — the failure mode biases toward hearing her.
        """
        token = voice_call.call_started("Avery")
        identity, _ = voice_call._ACTIVE_CALLS[token]
        voice_call._ACTIVE_CALLS[token] = (
            identity, 0.0,  # epoch 0 — older than any stale window
        )
        self.assertEqual(voice_call.active_calls(), [])


class CallActiveEndpointTests(unittest.TestCase):
    def setUp(self):
        voice_call._ACTIVE_CALLS.clear()

    tearDown = setUp

    def _get(self, identity=None):
        import json

        from api.voice import call_active

        response = asyncio.run(call_active(identity=identity))
        return json.loads(response.body)

    def test_reports_idle(self):
        self.assertEqual(
            self._get(), {"active": False, "identities": []}
        )

    def test_reports_busy(self):
        voice_call.call_started("Avery")
        payload = self._get()
        self.assertTrue(payload["active"])
        self.assertEqual(payload["identities"], ["Avery"])

    def test_identity_filter_isolates_the_boys(self):
        """Claude on a call must not keep Avery's Ear off its own mic."""
        voice_call.call_started("Claude")
        self.assertFalse(self._get(identity="Avery")["active"])
        self.assertTrue(self._get(identity="Claude")["active"])

    def test_identity_filter_is_case_insensitive(self):
        voice_call.call_started("Avery")
        self.assertTrue(self._get(identity="avery")["active"])


if __name__ == "__main__":
    unittest.main()
