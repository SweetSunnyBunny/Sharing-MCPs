"""Reusable test live message injection support."""

import asyncio
import json
import logging
import unittest
from unittest.mock import Mock, patch

from services import claude_subprocess
from services.chat_flow import StreamWebSocketBridge


async def _make_session(identity="Avery", conversation_id="conv-inject"):
    with patch.object(claude_subprocess.ClaudeSession, "_spawn", return_value=None):
        session, _fresh = await claude_subprocess._get_or_spawn(
            identity=identity,
            conversation_id=conversation_id,
            model="claude-opus-5",
            permission_mode="auto",
            effort="medium",
        )
    session.proc = Mock()
    session.proc.stdin = Mock()
    session.proc.poll.return_value = None
    session.dead = False
    return session


class InjectUserMessageTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_writes_one_ndjson_user_line_when_a_turn_is_in_flight(self):
        session = await _make_session()
        async with session.turn_lock:  # a turn is running
            ok = claude_subprocess.inject_user_message(
                "Avery", "conv-inject", "wait - also check the kettle",
            )
        self.assertTrue(ok)
        raw = session.proc.stdin.write.call_args[0][0]
        self.assertIsInstance(raw, bytes)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.count(b"\n"), 1)
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(payload["type"], "user")
        self.assertEqual(payload["message"]["role"], "user")
        self.assertEqual(
            payload["message"]["content"], "wait - also check the kettle",
        )
        self.assertTrue(session.proc.stdin.flush.called)

    async def test_refuses_when_the_session_is_idle(self):
        """An idle session must NOT be written to: the CLI would hold the
        message and replay it against whatever turn comes next, out of order.
        """
        session = await _make_session(conversation_id="conv-idle")
        ok = claude_subprocess.inject_user_message("Avery", "conv-idle", "hi")
        self.assertFalse(ok)
        self.assertFalse(session.proc.stdin.write.called)

    async def test_refuses_for_a_different_identity_or_conversation(self):
        session = await _make_session(conversation_id="conv-mine")
        async with session.turn_lock:
            self.assertFalse(
                claude_subprocess.inject_user_message("Claude", "conv-mine", "hi")
            )
            self.assertFalse(
                claude_subprocess.inject_user_message("Avery", "conv-theirs", "hi")
            )
        self.assertFalse(session.proc.stdin.write.called)

    async def test_refuses_a_dead_session(self):
        session = await _make_session(conversation_id="conv-dead")
        async with session.turn_lock:
            session.dead = True
            ok = claude_subprocess.inject_user_message("Avery", "conv-dead", "hi")
        self.assertFalse(ok)

    async def test_broken_pipe_returns_false_instead_of_raising(self):
        """A failed write must fall back to the queue, never blow up the
        listener task that owns the whole in-stream websocket read loop.
        """
        session = await _make_session(conversation_id="conv-brokenpipe")
        session.proc.stdin.write.side_effect = BrokenPipeError()
        async with session.turn_lock:
            ok = claude_subprocess.inject_user_message(
                "Avery", "conv-brokenpipe", "hi",
            )
        self.assertFalse(ok)

    async def test_empty_text_is_never_injected(self):
        session = await _make_session(conversation_id="conv-empty")
        async with session.turn_lock:
            self.assertFalse(
                claude_subprocess.inject_user_message("Avery", "conv-empty", "   ")
            )
        self.assertFalse(session.proc.stdin.write.called)

    async def test_no_session_at_all_is_a_clean_false(self):
        """Other backends (PTY, direct API, codex) keep no entry in _sessions,
        so they simply fall through to the queue.
        """
        self.assertFalse(
            claude_subprocess.inject_user_message("Nobody", "conv-none", "hi")
        )


async def _collect(event, session):
    return [
        e async for e in claude_subprocess._parse_event(event, session, "Claude", [])
    ]


class ExactReadMomentTests(unittest.IsolatedAsyncioTestCase):
    'Exactreadmomenttests.'

    def tearDown(self):
        claude_subprocess.kill_all_sessions()

    async def test_injection_tags_the_line_and_remembers_the_uuid(self):
        session = await _make_session(identity="Claude", conversation_id="conv-uuid")
        async with session.turn_lock:
            ok = claude_subprocess.inject_user_message(
                "Claude", "conv-uuid", "look at this", "uuid-123",
            )
        self.assertTrue(ok)
        payload = json.loads(session.proc.stdin.write.call_args[0][0].decode("utf-8"))
        self.assertEqual(payload["uuid"], "uuid-123")
        self.assertIn("uuid-123", session.injected_uuids)

    async def test_untagged_injection_writes_no_uuid(self):
        session = await _make_session(identity="Claude", conversation_id="conv-nouuid")
        async with session.turn_lock:
            claude_subprocess.inject_user_message("Claude", "conv-nouuid", "hi")
        payload = json.loads(session.proc.stdin.write.call_args[0][0].decode("utf-8"))
        self.assertNotIn("uuid", payload)
        self.assertEqual(session.injected_uuids, set())

    async def test_failed_write_forgets_the_uuid(self):
        session = await _make_session(identity="Claude", conversation_id="conv-fail")
        session.proc.stdin.write.side_effect = BrokenPipeError()
        async with session.turn_lock:
            ok = claude_subprocess.inject_user_message(
                "Claude", "conv-fail", "hi", "uuid-gone",
            )
        self.assertFalse(ok)
        self.assertEqual(session.injected_uuids, set())

    async def test_replay_of_a_slipped_in_message_yields_the_read_moment_once(self):
        session = await _make_session(identity="Claude", conversation_id="conv-replay")
        session.injected_uuids.add("uuid-abc")
        replay = {
            "type": "user", "isReplay": True, "uuid": "uuid-abc",
            "message": {"role": "user", "content": "hi"},
        }
        self.assertEqual(
            await _collect(replay, session),
            [{"type": "user_message_absorbed", "uuid": "uuid-abc"}],
        )
        self.assertEqual(session.injected_uuids, set())
        # A second echo of the same uuid is silent.
        self.assertEqual(await _collect(replay, session), [])

    async def test_replay_of_the_turns_own_message_is_swallowed(self):
        """Every stdin message replays, including the normal turn message,
        whose content is a plain STRING. It must never reach the tool-result
        parser (iterating a string there would crash the turn)."""
        session = await _make_session(identity="Claude", conversation_id="conv-own")
        replay = {
            "type": "user", "isReplay": True, "uuid": "someone-else",
            "message": {"role": "user", "content": "the whole prompt"},
        }
        self.assertEqual(await _collect(replay, session), [])

    async def test_string_content_user_event_without_replay_flag_is_harmless(self):
        session = await _make_session(identity="Claude", conversation_id="conv-str")
        evt = {"type": "user", "message": {"role": "user", "content": "plain"}}
        self.assertEqual(await _collect(evt, session), [])

    async def test_tool_results_still_parse(self):
        session = await _make_session(identity="Claude", conversation_id="conv-tool")
        evt = {
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "done"},
            ]},
        }
        out = await _collect(evt, session)
        self.assertEqual(out[0]["type"], "tool_result")
        self.assertEqual(out[0]["tool_use_id"], "t1")

    async def test_cli_is_launched_with_replay_enabled(self):
        session = await _make_session(identity="Claude", conversation_id="conv-cmd")
        with patch.object(claude_subprocess, "build_system_identity_prompt", return_value="x"), \
             patch.object(claude_subprocess, "write_claude_mcp_config", return_value=None):
            cmd = session._build_cmd()
        self.assertIn("--replay-user-messages", cmd)
        self.assertIn("--input-format", cmd)


class AttachmentSlipInTests(unittest.TestCase):
    'Attachmentslipintests.'

    def test_image_notes_skip_bad_names_and_suffixes(self):
        from services.chat_turn_prep import build_cli_image_notes
        notes = build_cli_image_notes([
            {"filename": "a.png"}, {"filename": ""}, {"filename": "evil.exe"},
            {"filename": "../../sneaky.jpg"},
        ])
        self.assertEqual(len(notes), 2)
        self.assertIn("a.png", notes[0])
        self.assertIn("Use the Read tool", notes[0])
        # Path traversal is flattened to the bare filename inside IMAGES_DIR.
        self.assertNotIn("..", notes[1])

    def test_compose_stacks_audio_docs_images_then_text(self):
        from services import chat_turn_prep
        with patch.object(chat_turn_prep, "build_document_note", return_value="[DOC]"), \
             patch.object(chat_turn_prep, "build_audio_note", return_value="[AUDIO]"):
            prompt = chat_turn_prep.compose_attachment_prompt(
                "hello", ["[IMG]"], [{"filename": "d.pdf"}], [{"filename": "v.webm"}],
            )
        self.assertEqual(prompt, "[AUDIO]\n\n[DOC]\n\n[IMG]\n\nhello")

    def test_compose_with_no_text_is_just_the_notes(self):
        from services import chat_turn_prep
        with patch.object(chat_turn_prep, "build_audio_note", return_value="[AUDIO]"):
            prompt = chat_turn_prep.compose_attachment_prompt(
                "", [], [], [{"filename": "v.webm"}],
            )
        self.assertEqual(prompt, "[AUDIO]")

    def test_compose_with_text_only_is_unchanged(self):
        from services.chat_turn_prep import compose_attachment_prompt
        self.assertEqual(compose_attachment_prompt("just words", [], [], []), "just words")


class _FakeWs:
    """Feeds the bridge a scripted set of frames, then blocks forever."""

    def __init__(self, frames):
        self._frames = list(frames)

    async def receive_text(self):
        if self._frames:
            return json.dumps(self._frames.pop(0))
        await asyncio.sleep(3600)


class BridgeInjectHookTests(unittest.IsolatedAsyncioTestCase):
    async def _run_listener(self, bridge, settle=0.2):
        task = asyncio.create_task(bridge.listen_during_stream())
        await asyncio.sleep(settle)
        await bridge.stop_listener(task)

    async def test_injected_message_is_not_also_queued(self):
        bridge = StreamWebSocketBridge(
            _FakeWs([{"type": "message", "content": "one more thing"}]),
            logging.getLogger("test"),
        )
        seen = []

        async def hook(incoming):
            seen.append(incoming)
            return True

        bridge.inject_hook = hook
        await self._run_listener(bridge)

        self.assertEqual(len(seen), 1)
        self.assertEqual(list(bridge.queued_messages), [])

    async def test_declined_message_falls_back_to_the_queue(self):
        msg = {
            "type": "message",
            "content": "with a photo",
            "images": [{"filename": "x.png"}],
        }
        bridge = StreamWebSocketBridge(_FakeWs([msg]), logging.getLogger("test"))

        async def hook(incoming):
            return False

        bridge.inject_hook = hook
        await self._run_listener(bridge)

        self.assertEqual(list(bridge.queued_messages), [msg])

    async def test_hook_exception_still_queues_the_message(self):
        msg = {"type": "message", "content": "boom"}
        bridge = StreamWebSocketBridge(_FakeWs([msg]), logging.getLogger("test"))

        async def hook(incoming):
            raise RuntimeError("stdin exploded")

        bridge.inject_hook = hook
        await self._run_listener(bridge)

        self.assertEqual(list(bridge.queued_messages), [msg])

    async def test_no_hook_configured_behaves_exactly_as_before(self):
        msg = {"type": "message", "content": "legacy path"}
        bridge = StreamWebSocketBridge(_FakeWs([msg]), logging.getLogger("test"))
        await self._run_listener(bridge)
        self.assertEqual(list(bridge.queued_messages), [msg])

    async def test_stop_streaming_still_cancels_and_is_never_injected(self):
        """The Stop button must keep working: it is the one path that SHOULD
        interrupt him, and it must never be mistaken for a slip-in.
        """
        bridge = StreamWebSocketBridge(
            _FakeWs([{"type": "stop_streaming"}]),
            logging.getLogger("test"),
        )
        called = []

        async def hook(incoming):
            called.append(incoming)
            return True

        bridge.inject_hook = hook
        task = asyncio.create_task(bridge.listen_during_stream())
        await asyncio.sleep(0.2)
        was_cancelled = await bridge.stop_listener(task)

        self.assertTrue(was_cancelled)
        self.assertEqual(called, [])
        self.assertEqual(list(bridge.queued_messages), [])

    async def test_non_message_frames_are_never_injected(self):
        """switch_identity / new_conversation / slash_command etc. are control
        frames, not things to whisper into a running turn.
        """
        frames = [
            {"type": "switch_identity", "identity": "Claude"},
            {"type": "slash_command", "command": "hub"},
        ]
        bridge = StreamWebSocketBridge(_FakeWs(frames), logging.getLogger("test"))
        called = []

        async def hook(incoming):
            called.append(incoming)
            return True

        bridge.inject_hook = hook
        await self._run_listener(bridge)

        self.assertEqual(called, [])
        self.assertEqual(len(bridge.queued_messages), 2)


if __name__ == "__main__":
    unittest.main()
