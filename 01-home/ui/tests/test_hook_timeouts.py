'Application module.'

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite

from services import context_hooks as ch


def _hook(name, build, **kw):
    return ch.ContextHook(name=name, order=kw.pop("order", 50), build=build, cache_ttl=kw.pop("cache_ttl", 0), **kw)


async def _fast(ctx):
    return "[fast hook says hi]"


async def _slow(ctx):
    await asyncio.sleep(0.4)
    return "[slow hook finally answered]"


async def _raises_own_timeout(ctx):
    raise TimeoutError("socket timed out inside the hook")


class HookTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        # Isolate all mutable hook state between retry/cache tests.
        self._state_patch = patch.multiple(ch, _hook_timings={}, _hook_cache={})
        self._state_patch.start()
        # Keep the build hermetic: no provider lookups, no ledger writes.
        self._patches = [
            patch("services.provider_router.resolve_provider_for_identity",
                  new=AsyncMock(return_value=("claude-code", {"backend": "subprocess"}))),
            patch("services.context_ledger.remember_orientation", new=lambda **kw: None),
        ]
        for p in self._patches:
            p.start()

    async def asyncTearDown(self):
        for p in self._patches:
            p.stop()
        self._state_patch.stop()
        await self.db.close()

    async def _build(self, registry):
        by_name = {h.name: h for h in registry}
        with patch.object(ch, "HOOK_REGISTRY", registry), patch.object(ch, "_HOOKS_BY_NAME", by_name):
            ctx = ch.HookContext(db=self.db, identity="Claude")
            return await ch.build_orientation_from_hooks(ctx)

    async def test_slow_hook_is_cut_off_and_named_while_the_rest_arrive(self):
        registry = [
            _hook("fast_one", _fast, order=10),
            _hook("slow_one", _slow, order=20, timeout_s=0.05),
        ]
        loop = asyncio.get_running_loop()
        started = loop.time()
        text = await self._build(registry)
        elapsed = loop.time() - started

        self.assertLess(elapsed, 0.35, "the turn waited on the hung hook")
        self.assertIn("[fast hook says hi]", text)
        self.assertNotIn("finally answered", text)
        self.assertIn("slow_one: didn't answer within 0.05s", text)
        self.assertIn("honest gap", text)

        report = {r["hook"]: r for r in ch.hook_timing_report()["hooks"]}
        self.assertEqual(report["slow_one"]["timeouts"], 1)
        self.assertEqual(report["fast_one"]["timeouts"], 0)
        self.assertEqual(report["fast_one"]["count"], 1)

    async def test_timed_out_hook_is_not_cached(self):
        registry = [_hook("slow_cached", _slow, cache_ttl=300, timeout_s=0.05)]
        with patch.object(ch, "_cache_set") as cache_set:
            await self._build(registry)
        cache_set.assert_not_called()

    async def test_consumptive_hook_is_never_cut_off(self):
        registry = [_hook("consumes_on_read", _slow, timeout_s=0)]
        with patch.object(ch, "_DEFAULT_HOOK_TIMEOUT_S", 0.05):
            text = await self._build(registry)
        self.assertIn("[slow hook finally answered]", text)
        self.assertNotIn("didn't answer", text)

    async def test_default_limit_applies_when_hook_sets_none(self):
        registry = [_hook("uses_default", _slow)]
        with patch.object(ch, "_DEFAULT_HOOK_TIMEOUT_S", 0.05):
            text = await self._build(registry)
        self.assertIn("uses_default: didn't answer within 0.05s", text)

    async def test_timeout_raised_inside_a_hook_is_an_error_not_a_cutoff(self):
        registry = [_hook("own_timeout", _raises_own_timeout, timeout_s=5)]
        text = await self._build(registry)
        self.assertNotIn("didn't answer", text)
        report = {r["hook"]: r for r in ch.hook_timing_report()["hooks"]}
        self.assertEqual(report["own_timeout"]["errors"], 1)
        self.assertEqual(report["own_timeout"]["timeouts"], 0)

    async def test_explicit_limit_overrides_shorter_default_and_is_reported(self):
        async def finishes(ctx):
            await asyncio.sleep(0.03)
            return "[completed]"
        registry = [_hook("explicit", finishes, timeout_s=1)]
        with patch.object(ch, "_DEFAULT_HOOK_TIMEOUT_S", 0.001):
            text = await self._build(registry)
            with patch.object(ch, "_HOOKS_BY_NAME", {"explicit": registry[0]}):
                row = ch.hook_timing_report()["hooks"][0]
        self.assertIn("[completed]", text)
        self.assertEqual(row["limit_s"], 1)
        self.assertEqual(row["timeouts"], 0)

    async def test_timeout_retries_then_caches_only_success(self):
        calls = 0
        async def retries(ctx):
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.Event().wait()
            return "[recovered]"
        registry = [_hook("retry", retries, cache_ttl=300, timeout_s=0.02)]
        first = await self._build(registry)
        self.assertIn("honest gap", first)
        self.assertFalse(ch._hook_cache)
        self.assertIn("[recovered]", await self._build(registry))
        self.assertIn("[recovered]", await self._build(registry))
        self.assertEqual(calls, 2)
        row = ch.hook_timing_report()["hooks"][0]
        self.assertEqual(row["count"], 2)  # cache hit is not another run
        self.assertEqual(row["timeouts"], 1)
        self.assertEqual(row["errors"], 0)

    async def test_error_fails_open_is_not_cached_and_retries(self):
        build = AsyncMock(side_effect=[RuntimeError("unavailable"), "[recovered]"])
        registry = [_hook("error_retry", build, cache_ttl=300)]
        self.assertEqual(await self._build(registry), "")
        self.assertFalse(ch._hook_cache)
        self.assertIn("[recovered]", await self._build(registry))
        row = ch.hook_timing_report()["hooks"][0]
        self.assertEqual(row["errors"], 1)
        self.assertEqual(row["timeouts"], 0)

    async def test_parent_cancellation_propagates_without_caching(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        async def waits(ctx):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        task = asyncio.create_task(self._build([_hook("cancel", waits, cache_ttl=300)]))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        self.assertFalse(ch._hook_cache)
        self.assertFalse(ch._hook_timings)

    async def test_session_note_survives_consumptive_write_past_default(self):
        import json
        import os
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        # Replace only the final consuming write, allowing it to finish after
        # the default deadline. The real hook must return the consumed note.
        original_replace = os.replace
        async def threaded(func, *args, **kwargs):
            if func is original_replace:
                await asyncio.sleep(0.03)
            return func(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            note = folder / "session_notes.json"
            note.write_text(json.dumps({"Claude": {
                "note": "Keep this handoff",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }}), encoding="utf-8")
            with patch.object(ch, "RITUALS_DIR", folder), patch.object(
                ch, "_DEFAULT_HOOK_TIMEOUT_S", 0.001
            ), patch.object(ch.asyncio, "to_thread", side_effect=threaded):
                text = await self._build([ch.get_hook("session_notes")])
            self.assertIn("Keep this handoff", text)
            self.assertNotIn("honest gap", text)
            self.assertNotIn("Claude", json.loads(note.read_text(encoding="utf-8")))
        self.assertEqual(ch.hook_timing_report()["hooks"][0]["timeouts"], 0)


class HookRegistryTimeoutTests(unittest.TestCase):
    def test_consumptive_hooks_are_exempt(self):
        # Cancelling these mid-read would lose words (pings are deleted on
        # read; crossfeed advances a watermark; session notes delete on return).
        for name in ("commons_pings", "room_crossfeed", "session_notes"):
            self.assertEqual(ch.get_hook(name).timeout_s, 0, name)

    def test_default_limit_is_generous_until_measured(self):
        self.assertGreaterEqual(ch._DEFAULT_HOOK_TIMEOUT_S, 10)


if __name__ == "__main__":
    unittest.main()
