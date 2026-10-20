import time
import unittest
from types import SimpleNamespace

from api import chat_http


class ChatHttpSessionStateTests(unittest.TestCase):
    def setUp(self):
        chat_http._http_sessions.clear()

    def tearDown(self):
        chat_http._http_sessions.clear()

    def test_anonymous_requests_get_unique_http_session_keys(self):
        request_a = SimpleNamespace(cookies={})
        request_b = SimpleNamespace(cookies={})

        state_a, cookie_a = chat_http._resolve_http_session(request_a)
        state_b, cookie_b = chat_http._resolve_http_session(request_b)

        self.assertIsNot(state_a, state_b)
        self.assertTrue(cookie_a)
        self.assertTrue(cookie_b)
        self.assertNotEqual(cookie_a, cookie_b)

    def test_http_sessions_are_pruned_after_ttl(self):
        chat_http._http_sessions["anon:old"] = {
            "state": chat_http._new_http_session_state(),
            "last_seen_at": time.monotonic() - (chat_http._HTTP_SESSION_TTL_SECONDS + 5),
        }
        chat_http._http_sessions["anon:fresh"] = {
            "state": chat_http._new_http_session_state(),
            "last_seen_at": time.monotonic(),
        }

        chat_http._prune_http_sessions()

        self.assertNotIn("anon:old", chat_http._http_sessions)
        self.assertIn("anon:fresh", chat_http._http_sessions)
