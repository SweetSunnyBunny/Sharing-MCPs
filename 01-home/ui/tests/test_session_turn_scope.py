"""Session/turn orientation split (#14).

`is_any_session_warm()` tells the hook builder whether a live -p session for
this identity/conversation has already had its first real turn — if so, the
scope='session' hooks (narrative/pack recall the live process already holds)
skip entirely. Model-agnostic and fail-safe: any lookup miss or a provider
that never touches the -p pool leaves the turn treated as cold, which just
means every hook runs, same as pre-#14 behavior.
"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from services import claude_subprocess
from services.context_hooks import ContextHook, HookContext, build_orientation_from_hooks


def _make_session(**overrides):
    async def _build():
        with patch.object(claude_subprocess.ClaudeSession, "_spawn", return_value=None):
            session, _fresh = await claude_subprocess._get_or_spawn(
                identity=overrides.get("identity", "Claude"),
                conversation_id=overrides.get("conversation_id", "conv-scope"),
                model=overrides.get("model", "claude-opus-4-8"),
                permission_mode="auto",
                effort="medium",
            )
        session.dead = False
        session.first_turn = overrides.get("first_turn", True)
        return session
    return _build()


class IsAnySessionWarmTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_no_session_is_cold(self):
        self.assertFalse(claude_subprocess.is_any_session_warm("Claude", "no-such-conv"))

    async def test_fresh_session_first_turn_true_is_cold(self):
        await _make_session(identity="Claude", conversation_id="c1", first_turn=True)
        self.assertFalse(claude_subprocess.is_any_session_warm("Claude", "c1"))

    async def test_session_past_first_turn_is_warm(self):
        await _make_session(identity="Claude", conversation_id="c2", first_turn=False)
        self.assertTrue(claude_subprocess.is_any_session_warm("Claude", "c2"))

    async def test_dead_session_is_cold(self):
        session = await _make_session(identity="Claude", conversation_id="c3", first_turn=False)
        session.dead = True
        self.assertFalse(claude_subprocess.is_any_session_warm("Claude", "c3"))

    async def test_model_agnostic_matches_any_model_for_identity_and_conversation(self):
        await _make_session(identity="Claude", conversation_id="c4", model="claude-fable-5", first_turn=False)
        self.assertTrue(claude_subprocess.is_any_session_warm("Claude", "c4"))

    async def test_scoped_to_identity_and_conversation(self):
        await _make_session(identity="Claude", conversation_id="c5", first_turn=False)
        self.assertFalse(claude_subprocess.is_any_session_warm("Avery", "c5"))
        self.assertFalse(claude_subprocess.is_any_session_warm("Claude", "different-conv"))


class ScopeFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_scoped_hook_runs_when_cold(self):
        hook = ContextHook(name="narrative_recall", order=1, build=AsyncMock(return_value="recall block"), scope="session")
        with patch("services.context_hooks.HOOK_REGISTRY", [hook]):
            ctx = HookContext(db=None, identity="Claude", is_warm_turn=False)
            result = await build_orientation_from_hooks(ctx)
        self.assertIn("recall block", result)

    async def test_session_scoped_hook_skipped_when_warm(self):
        hook = ContextHook(name="narrative_recall", order=1, build=AsyncMock(return_value="recall block"), scope="session")
        with patch("services.context_hooks.HOOK_REGISTRY", [hook]):
            ctx = HookContext(db=None, identity="Claude", is_warm_turn=True)
            result = await build_orientation_from_hooks(ctx)
        self.assertEqual(result, "")

    async def test_both_scoped_hook_always_runs(self):
        hook = ContextHook(name="ground_check", order=1, build=AsyncMock(return_value="ground block"), scope="both")
        with patch("services.context_hooks.HOOK_REGISTRY", [hook]):
            ctx = HookContext(db=None, identity="Claude", is_warm_turn=True)
            result = await build_orientation_from_hooks(ctx)
        self.assertIn("ground block", result)


class BuildOrientationWarmLookupTests(unittest.IsolatedAsyncioTestCase):
    """build_orientation_context (session_lifecycle) computes is_warm_turn
    via a best-effort lookup and never lets that lookup break the turn."""

    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_lookup_failure_defaults_to_cold(self):
        from services import session_lifecycle

        db = Mock()
        with patch("services.session_lifecycle.get_last_message_preview", AsyncMock(return_value=None)), \
             patch("services.provider_router.resolve_provider_for_identity", AsyncMock(return_value=("claude-code", {}))), \
             patch("services.claude_subprocess.is_any_session_warm", side_effect=RuntimeError("boom")), \
             patch("services.context_hooks.build_orientation_from_hooks", AsyncMock(return_value="")) as mock_build:
            await session_lifecycle.build_orientation_context(
                db=db, conversation_id="conv-x", identity="Claude",
            )
        ctx_passed = mock_build.call_args[0][0]
        self.assertFalse(ctx_passed.is_warm_turn)

    async def test_codex_saved_thread_marks_turn_warm(self):
        from services import session_lifecycle

        db = Mock()
        with patch("services.session_lifecycle.get_last_message_preview", AsyncMock(return_value=None)), \
             patch("services.provider_router.resolve_provider_for_identity", AsyncMock(return_value=("codex", {}))), \
             patch("services.session_manager.get_provider_session_id_from_db", AsyncMock(return_value="thread-sol")) as lookup, \
             patch("services.context_hooks.build_orientation_from_hooks", AsyncMock(return_value="")) as mock_build:
            await session_lifecycle.build_orientation_context(
                db=db, conversation_id="conv-sol", identity="Avery",
            )

        lookup.assert_awaited_once_with(db, "conv-sol", "codex")
        ctx_passed = mock_build.call_args[0][0]
        self.assertTrue(ctx_passed.is_warm_turn)


if __name__ == "__main__":
    unittest.main()
