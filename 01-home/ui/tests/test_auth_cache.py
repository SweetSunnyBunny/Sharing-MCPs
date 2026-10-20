import unittest
from datetime import datetime, timezone

from services import auth_cache


class AuthCacheTests(unittest.TestCase):
    def setUp(self):
        auth_cache._auth_cache.clear()

    def tearDown(self):
        auth_cache._auth_cache.clear()

    def test_cache_and_lookup_valid_token(self):
        token = "token-1"
        future_epoch = int(datetime.now(timezone.utc).timestamp()) + 3600
        auth_cache.cache_session_token(token, future_epoch)

        expiry = auth_cache.get_cached_session_expiry(token)
        self.assertEqual(expiry, future_epoch)

    def test_lookup_rejects_expired_session_epoch(self):
        token = "token-2"
        past_epoch = int(datetime.now(timezone.utc).timestamp()) - 1
        # Directly seed an expired entry with a future cache TTL to ensure
        # session-expiry checking is what rejects it.
        auth_cache._auth_cache[token] = (10**12, past_epoch)

        expiry = auth_cache.get_cached_session_expiry(token)
        self.assertIsNone(expiry)
        self.assertNotIn(token, auth_cache._auth_cache)

    def test_cache_ignores_already_expired_session(self):
        token = "token-3"
        past_epoch = int(datetime.now(timezone.utc).timestamp()) - 60
        auth_cache.cache_session_token(token, past_epoch)
        self.assertIsNone(auth_cache.get_cached_session_expiry(token))

    def test_revoke_session_token_removes_entry(self):
        token = "token-4"
        future_epoch = int(datetime.now(timezone.utc).timestamp()) + 3600
        auth_cache.cache_session_token(token, future_epoch)
        self.assertIsNotNone(auth_cache.get_cached_session_expiry(token))

        auth_cache.revoke_session_token(token)
        self.assertIsNone(auth_cache.get_cached_session_expiry(token))

