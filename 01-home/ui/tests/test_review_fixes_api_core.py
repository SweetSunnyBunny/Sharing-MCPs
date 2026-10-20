'Application module.'

import inspect
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


def _gated_app():
    """A bare app behind the real auth middleware with two stand-in routes."""
    from core import middleware

    app = FastAPI()
    app.middleware("http")(middleware.auth_and_cache_middleware)

    @app.post("/api/wearable/reply")
    async def _reply():
        return {"ok": True}

    @app.get("/api/room/tap/{room}")
    async def _tap(room: str):
        return {"room": room}

    return app


class MiddlewareCarveOutTests(unittest.TestCase):
    def setUp(self):
        from core import middleware

        self._patches = [
            mock.patch.object(middleware, "AUTH_ENABLED", True),
            mock.patch.object(middleware, "PUBLIC_BASE_URL", ""),
            mock.patch.object(middleware, "_ANAM_API_KEY", "test-key"),
        ]
        for p in self._patches:
            p.start()
        self.client = TestClient(_gated_app())

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_wearable_reply_requires_auth(self):
        r = self.client.post("/api/wearable/reply", json={"text": "hi"})
        self.assertEqual(r.status_code, 401)

    def test_wearable_reply_accepts_bearer_key(self):
        r = self.client.post(
            "/api/wearable/reply",
            json={"text": "hi"},
            headers={"Authorization": "Bearer test-key"},
        )
        self.assertEqual(r.status_code, 200)

    def test_room_tap_stays_public(self):
        r = self.client.get("/api/room/tap/nest")
        self.assertEqual(r.status_code, 200)


class RoomTapSlugTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_slugs_are_recorded(self):
        from api import room as room_api

        for slug in ("nest", "bathroom", "office", "kitchen", "dining_room"):
            with mock.patch.object(room_api, "record_room", new=mock.AsyncMock()) as rec:
                resp = await room_api.tap_room(slug)
            self.assertEqual(resp.status_code, 200, slug)
            rec.assert_awaited_once()
            self.assertEqual(rec.await_args.args[0].room, slug)

    async def test_uppercase_tag_is_normalized(self):
        from api import room as room_api

        with mock.patch.object(room_api, "record_room", new=mock.AsyncMock()) as rec:
            resp = await room_api.tap_room("Nest")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(rec.await_args.args[0].room, "nest")

    async def test_hostile_slugs_are_rejected_and_never_recorded(self):
        from api import room as room_api

        for slug in (
            "<script>alert(1)</script>",
            "nest<img src=x onerror=alert(1)>",
            "a" * 65,
            "",
            "bath room",
            "neést",
        ):
            with mock.patch.object(room_api, "record_room", new=mock.AsyncMock()) as rec:
                resp = await room_api.tap_room(slug)
            self.assertEqual(resp.status_code, 404, repr(slug))
            rec.assert_not_awaited()
            self.assertNotIn(b"<script", resp.body)


class SettingsEffortTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_effort_default_is_config_effort_not_low(self):
        import config
        from api import settings as settings_api

        async def _default(_key, default):
            return default

        with mock.patch.object(settings_api, "_get_setting", new=_default):
            result = await settings_api.get_effort()
        self.assertEqual(result["effort"], config.CLAUDE_EFFORT)
        self.assertNotEqual(result["effort"], "low")

    async def test_effort_routes_reject_bad_payloads_with_400(self):
        from api import settings as settings_api

        class _Req:
            def __init__(self, body):
                self._body = body

            async def json(self):
                return self._body

        for route in (settings_api.set_effort, settings_api.set_fable_effort):
            for body in (["high"], "high", 5, {"effort": 7}, {"effort": None}):
                with mock.patch.object(settings_api, "_set_setting", new=mock.AsyncMock()) as save:
                    resp = await route(_Req(body))
                self.assertIsInstance(resp, JSONResponse, (route.__name__, body))
                self.assertEqual(resp.status_code, 400, (route.__name__, body))
                save.assert_not_awaited()

    async def test_set_model_rejects_non_object_body(self):
        from api import settings as settings_api

        class _Req:
            async def json(self):
                return ["claude-opus-5"]

        with mock.patch.object(settings_api, "_set_setting", new=mock.AsyncMock()) as save:
            resp = await settings_api.set_model(_Req())
        self.assertEqual(resp.status_code, 400)
        save.assert_not_awaited()

    async def test_set_effort_does_not_mutate_config(self):
        import config
        from api import settings as settings_api

        class _Req:
            async def json(self):
                return {"effort": "max"}

        before = config.CLAUDE_EFFORT
        with mock.patch.object(settings_api, "_set_setting", new=mock.AsyncMock()), \
             mock.patch.object(settings_api, "_retire_claude_code_sessions"):
            result = await settings_api.set_effort(_Req())
        self.assertEqual(result, {"ok": True, "effort": "max"})
        self.assertEqual(config.CLAUDE_EFFORT, before)


class LogoutMethodTests(unittest.TestCase):
    def test_logout_is_post_only(self):
        from api.auth import router

        methods = set()
        for route in router.routes:
            if getattr(route, "path", "").endswith("/logout"):
                methods |= set(route.methods)
        self.assertEqual(methods, {"POST"})


class SanctuaryRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_portrait_redirect_quotes_mood_and_identity(self):
        from api import sanctuary

        resp = await sanctuary.get_portrait("avery/../x", mood="happy&admin=1#frag")
        location = resp.headers["location"]
        self.assertTrue(location.startswith(sanctuary.HEARTH_HUB + "/api/sanctuary/portrait/"))
        self.assertIn("avery%2F..%2Fx", location)
        self.assertIn("mood=happy%26admin%3D1%23frag", location)
        self.assertNotIn("#", location)


class CallLlmSecretCompareTests(unittest.TestCase):
    def test_uses_constant_time_compare(self):
        from api import call_llm

        src = inspect.getsource(call_llm.call_chat_completions)
        self.assertIn("hmac.compare_digest", src)
        self.assertNotIn("token != secret", src)


if __name__ == "__main__":
    unittest.main()
