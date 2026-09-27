"""Reusable test codex schedule fallback support."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from services import provider_router
from services.context_hooks import HookContext, _hook_ground_check


class _FableLimitStateMixin:
    """Reset the router's in-memory limit flag around every test."""

    def setUp(self):
        provider_router._fable_limited_until = 0.0
        self.addCleanup(
            setattr, provider_router, "_fable_limited_until", 0.0
        )


def _settings(**over):
    base = {
        "provider": "claude-code",
        "config": {},
        "effort": "medium",
        "model": "claude-opus-5",
        "autowake_model": "claude-sonnet-4-6",
        "identity_overrides": {},
    }
    base.update(over)
    return base


class GroundCheckOverrideTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_hop_reported_truthfully(self):
        """A gpt-* schedule override must show the Codex brain, not the
        global autowake lane."""
        with patch(
            "services.provider_router._load_settings",
            new=AsyncMock(return_value=_settings()),
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("claude-code", {})),
        ):
            ctx = HookContext(
                db=None, identity="River", mode="autonomous",
                model_override="gpt-5.6-sol",
            )
            out = await _hook_ground_check(ctx)

        self.assertIn("gpt-5.6-sol", out)
        self.assertIn("codex CLI (per-schedule override)", out)
        self.assertIn("n/a (codex)", out)
        self.assertNotIn("claude-sonnet-4-6", out)

    async def test_claude_override_and_effort_reported(self):
        """A Claude-model schedule override stays on claude-code but reports
        the overridden model and per-schedule effort."""
        with patch(
            "services.provider_router._load_settings",
            new=AsyncMock(return_value=_settings()),
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("claude-code", {})),
        ):
            ctx = HookContext(
                db=None, identity="River", mode="autonomous",
                model_override="claude-opus-4-8", effort_override="xhigh",
            )
            out = await _hook_ground_check(ctx)

        self.assertIn("claude-opus-4-8", out)
        self.assertIn("per-schedule model", out)
        self.assertIn("xhigh", out)

    async def test_no_override_keeps_global_lane(self):
        with patch(
            "services.provider_router._load_settings",
            new=AsyncMock(return_value=_settings()),
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("claude-code", {})),
        ):
            ctx = HookContext(db=None, identity="River", mode="autonomous")
            out = await _hook_ground_check(ctx)

        self.assertIn("claude-sonnet-4-6", out)
        self.assertNotIn("per-schedule", out)


def _stream_kwargs(**over):
    base = dict(
        db=None,
        identity="River",
        conv_id="conv-1",
        user_message="review the chapter",
        context_block="",
        session_name="Chapter Review",
        owner_connected=False,
        model_override="gpt-5.6-sol",
    )
    base.update(over)
    return base


class CodexFallbackTests(unittest.IsolatedAsyncioTestCase):
    def _patches(self, fake_get_stream_source):
        return (
            patch(
                "services.provider_router.get_stream_source",
                new=fake_get_stream_source,
            ),
            patch(
                "services.autowake.build_messages_array",
                new=AsyncMock(return_value=[]),
            ),
            patch("services.autowake.get_db", new=AsyncMock(return_value=None)),
            patch("services.autowake.release_db", new=AsyncMock()),
            patch(
                "services.skill_runtime.build_skill_injection",
                return_value=("", []),
            ),
            patch(
                "services.skill_runtime.build_skill_catalog_hint",
                return_value="",
            ),
        )

    async def _collect(self, fake_get_stream_source, **kwargs):
        from services import autowake
        p = self._patches(fake_get_stream_source)
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            events = []
            async for ev in autowake._stream_autonomous(
                **_stream_kwargs(**kwargs)
            ):
                events.append(ev)
        return events

    async def test_codex_error_before_output_falls_back(self):
        calls = []

        async def _failing():
            yield {"type": "error", "message": "codex login required"}

        async def _working():
            yield {"type": "stream_delta", "delta": "review done"}
            yield {"type": "stream_end", "full_content": "review done"}

        async def fake_source(**kwargs):
            calls.append(kwargs.get("model_override"))
            return _failing() if len(calls) == 1 else _working()

        events = await self._collect(fake_source)

        self.assertEqual(calls, ["gpt-5.6-sol", None])
        types = [e["type"] for e in events]
        self.assertNotIn("error", types)
        end = next(e for e in events if e["type"] == "stream_end")
        self.assertEqual(end["full_content"], "review done")

    async def test_codex_exception_before_output_falls_back(self):
        calls = []

        async def _working():
            yield {"type": "stream_end", "full_content": "made it"}

        async def fake_source(**kwargs):
            calls.append(kwargs.get("model_override"))
            if len(calls) == 1:
                raise RuntimeError("codex app-server stdin closed")
            return _working()

        events = await self._collect(fake_source)

        self.assertEqual(calls, ["gpt-5.6-sol", None])
        end = next(e for e in events if e["type"] == "stream_end")
        self.assertEqual(end["full_content"], "made it")

    async def test_no_fallback_after_output_started(self):
        """Once text has streamed, an error passes through — a retry would
        double-run tools/output."""
        calls = []

        async def _partial_then_error():
            yield {"type": "stream_delta", "delta": "half a review"}
            yield {"type": "error", "message": "codex crashed mid-turn"}

        async def fake_source(**kwargs):
            calls.append(kwargs.get("model_override"))
            return _partial_then_error()

        events = await self._collect(fake_source)

        self.assertEqual(calls, ["gpt-5.6-sol"])
        self.assertIn("error", [e["type"] for e in events])

    async def test_claude_override_error_passes_through(self):
        """The net is codex-only: a Claude-model override that errors gets no
        second attempt."""
        calls = []

        async def _failing():
            yield {"type": "error", "message": "boom"}

        async def fake_source(**kwargs):
            calls.append(kwargs.get("model_override"))
            return _failing()

        events = await self._collect(
            fake_source, model_override="claude-fable-5"
        )

        self.assertEqual(calls, ["claude-fable-5"])
        self.assertIn("error", [e["type"] for e in events])


class FableLimitFlagTests(_FableLimitStateMixin, unittest.IsolatedAsyncioTestCase):
    def test_mark_uses_cli_reset_epoch_when_sane(self):
        until = time.time() + 1800
        provider_router.mark_fable_limited(until)
        self.assertAlmostEqual(
            provider_router._fable_limited_until, until, delta=1
        )
        self.assertTrue(provider_router.fable_limited_active())

    def test_mark_clamps_garbage_epochs_to_an_hour(self):
        for bad in (0, time.time() - 100, time.time() + 7 * 3600, None):
            provider_router.mark_fable_limited(bad)
            remaining = provider_router._fable_limited_until - time.time()
            self.assertGreater(remaining, 3500)
            self.assertLess(remaining, 3700)

    def test_detection_arms_flag_for_fable_only(self):
        from services.claude_subprocess import _maybe_mark_fable_limited

        reset_epoch = int(time.time()) + 1200
        # Non-fable session: never arms, even with the limit text.
        _maybe_mark_fable_limited(
            f"Claude AI usage limit reached|{reset_epoch}",
            SimpleNamespace(model="claude-opus-5"),
        )
        self.assertFalse(provider_router.fable_limited_active())

        # Fable session with unrelated error text: never arms.
        _maybe_mark_fable_limited(
            "some other CLI failure",
            SimpleNamespace(model="claude-fable-5"),
        )
        self.assertFalse(provider_router.fable_limited_active())

        # Fable session with the limit text: arms until the CLI's epoch.
        _maybe_mark_fable_limited(
            f"Claude AI usage limit reached|{reset_epoch}",
            SimpleNamespace(model="claude-fable-5"),
        )
        self.assertTrue(provider_router.fable_limited_active())
        self.assertAlmostEqual(
            provider_router._fable_limited_until, reset_epoch, delta=1
        )

    async def test_router_demotes_fable_turns_while_limited(self):
        stream_mock = Mock(return_value=iter(()))
        settings = _settings(model="claude-fable-5")

        with patch.object(
            provider_router, "_load_settings", new=AsyncMock(return_value=settings)
        ), patch(
            "services.claude_subprocess.stream_claude", new=stream_mock
        ):
            await provider_router.get_stream_source(
                message="hi", identity="Claude", conversation_id="c1",
            )
            self.assertEqual(
                stream_mock.call_args.kwargs["model"], "claude-fable-5"
            )

            provider_router._fable_limited_until = time.time() + 600
            await provider_router.get_stream_source(
                message="hi again", identity="Claude", conversation_id="c1",
            )
            self.assertEqual(
                stream_mock.call_args.kwargs["model"], "claude-opus-5"
            )

    async def test_ground_check_reports_demotion(self):
        provider_router._fable_limited_until = time.time() + 600
        with patch(
            "services.provider_router._load_settings",
            new=AsyncMock(return_value=_settings(model="claude-fable-5")),
        ), patch(
            "services.provider_router.resolve_provider_for_identity",
            new=AsyncMock(return_value=("claude-code", {})),
        ):
            ctx = HookContext(db=None, identity="Claude", mode="interactive")
            out = await _hook_ground_check(ctx)

        self.assertIn("claude-opus-5", out)
        self.assertIn("Fable at usage limit, demoted", out)
        self.assertNotIn("Fable cost guard", out)


class FableLimitAutowakeRetryTests(
    _FableLimitStateMixin, CodexFallbackTests
):
    """Reuses CodexFallbackTests' patch harness; only adds the fable-trip
    retry cases. (Inherited codex tests re-run here with the flag reset —
    harmless duplication that also proves the two nets don't interfere.)"""

    async def test_fable_trip_mid_hour_retries_once(self):
        calls = []

        async def _limit_wall():
            provider_router._fable_limited_until = time.time() + 600
            yield {"type": "error", "message": "Claude AI usage limit reached|0"}

        async def _working():
            yield {"type": "stream_end", "full_content": "made it on opus"}

        async def fake_source(**kwargs):
            calls.append(kwargs.get("model_override"))
            return _limit_wall() if len(calls) == 1 else _working()

        events = await self._collect(fake_source, model_override=None)

        self.assertEqual(calls, [None, None])
        self.assertNotIn("error", [e["type"] for e in events])
        end = next(e for e in events if e["type"] == "stream_end")
        self.assertEqual(end["full_content"], "made it on opus")

    async def test_already_limited_error_does_not_loop(self):
        """If the flag was ALREADY armed before the attempt, a failure is not
        limit-related — no retry, no loop."""
        provider_router._fable_limited_until = time.time() + 600
        calls = []

        async def _failing():
            yield {"type": "error", "message": "some other failure"}

        async def fake_source(**kwargs):
            calls.append(kwargs.get("model_override"))
            return _failing()

        events = await self._collect(fake_source, model_override=None)

        self.assertEqual(calls, [None])
        self.assertIn("error", [e["type"] for e in events])


if __name__ == "__main__":
    unittest.main()
