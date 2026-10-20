import asyncio
import json
import unittest
from collections import deque

from services.chat_flow import (
    StreamAccumulator,
    StreamWebSocketBridge,
    build_assistant_message_metadata,
    build_user_message_metadata,
    merge_tool_result_entry,
    normalize_incoming_message,
)


class _FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(message % args if args else message)


class _FakeWebSocket:
    def __init__(self, incoming):
        self.incoming = deque(incoming)
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    async def receive_text(self):
        if not self.incoming:
            raise RuntimeError("socket closed")
        item = self.incoming.popleft()
        if isinstance(item, Exception):
            raise item
        return item


class ChatFlowHelperTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_incoming_message_supports_legacy_fields(self):
        text, images, documents, audio = normalize_incoming_message({
            "content": " hello ",
            "image": {"filename": "a.png"},
            "document": {"filename": "b.txt"},
            "documents": [{"filename": "valid.md"}, {"bad": True}],
        })

        self.assertEqual(text, "hello")
        self.assertEqual(images, [{"filename": "a.png"}])
        self.assertEqual(documents, [{"filename": "valid.md"}])
        self.assertEqual(audio, [])

    def test_normalize_incoming_message_accepts_audio(self):
        text, images, documents, audio = normalize_incoming_message({
            "content": "hi",
            "audio": [{"filename": "abc.mp3", "transcript": "hello"}],
        })
        self.assertEqual(text, "hi")
        self.assertEqual(audio, [{"filename": "abc.mp3", "transcript": "hello"}])

    def test_build_user_message_metadata(self):
        self.assertIsNone(build_user_message_metadata([], []))
        self.assertEqual(
            build_user_message_metadata(
                [{"filename": "image.png"}],
                [{"filename": "doc.txt"}],
            ),
            {
                "images": [{"filename": "image.png"}],
                "documents": [{"filename": "doc.txt"}],
                "document": {"filename": "doc.txt"},
            },
        )

    def test_build_user_message_metadata_with_audio(self):
        self.assertEqual(
            build_user_message_metadata(
                [],
                [],
                [{"filename": "voicememo.mp3", "transcript": "hey there"}],
            ),
            {
                "audio": [{"filename": "voicememo.mp3", "transcript": "hey there"}],
            },
        )

    def test_build_assistant_message_metadata(self):
        metadata = build_assistant_message_metadata(
            response_images=[{"url": "/api/images/file/x.png"}],
            response_documents=[{"filename": "doc.txt"}],
            thinking_blocks=["internal note"],
            tool_events=[{"tool_name": "bash", "tool_id": "tool-1"}],
            tool_results_map={
                "tool-1": {
                    "status": "completed",
                    "input": {"command": "dir"},
                    "content": "file-a\nfile-b",
                }
            },
            context_notice={"title": "Compaction in progress"},
        )

        self.assertEqual(metadata["images"][0]["url"], "/api/images/file/x.png")
        self.assertEqual(metadata["document"]["filename"], "doc.txt")
        self.assertEqual(metadata["thinking"], ["internal note"])
        self.assertEqual(metadata["tools"][0]["input"], {"command": "dir"})
        self.assertEqual(metadata["tools"][0]["content"], "file-a\nfile-b")
        self.assertEqual(metadata["context_notice"]["title"], "Compaction in progress")

    def test_merge_tool_result_entry_accumulates_details(self):
        tool_results_map = {}

        merge_tool_result_entry(
            tool_results_map,
            tool_id="tool-1",
            tool_name="shell",
            tool_input={"command": "dir"},
        )
        merge_tool_result_entry(
            tool_results_map,
            tool_id="tool-1",
            status="completed",
            content="file-a\nfile-b",
        )

        self.assertEqual(
            tool_results_map["tool-1"],
            {
                "tool_name": "shell",
                "input": {"command": "dir"},
                "status": "completed",
                "content": "file-a\nfile-b",
            },
        )

    def test_model_provenance_tracks_fable_handoff(self):
        acc = StreamAccumulator()
        acc.observe({
            "type": "meta",
            "provider": "claude-code",
            "requested_model": "claude-fable-5",
        })
        acc.observe({"type": "meta", "actual_model": "claude-fable-5"})
        acc.observe({"type": "meta", "actual_model": "claude-opus-4-8"})
        acc.observe({"type": "meta", "actual_model": "claude-opus-4-8"})

        self.assertEqual(
            acc.model_provenance(),
            {
                "provider": "claude-code",
                "requested_model": "claude-fable-5",
                "models_used": ["claude-fable-5", "claude-opus-4-8"],
                "actual_model": "claude-opus-4-8",
                "switched": True,
            },
        )

    async def test_stream_bridge_handles_ping_and_queues_other_messages(self):
        ws = _FakeWebSocket([
            json.dumps({"type": "ping"}),
            json.dumps({"type": "switch_identity", "identity": "Claude"}),
            RuntimeError("socket closed"),
        ])
        bridge = StreamWebSocketBridge(ws, _FakeLogger())

        await bridge.listen_during_stream()

        self.assertEqual(ws.sent, [{"type": "pong"}])
        self.assertEqual(
            list(bridge.queued_messages),
            [{"type": "switch_identity", "identity": "Claude"}],
        )
        self.assertFalse(bridge.ws_alive)

    async def test_stream_bridge_stops_on_stop_streaming(self):
        ws = _FakeWebSocket([json.dumps({"type": "stop_streaming"})])
        logger = _FakeLogger()
        bridge = StreamWebSocketBridge(ws, logger)

        await bridge.listen_during_stream()

        self.assertTrue(bridge.cancel_event.is_set())
        self.assertIn("Stop streaming requested by user", logger.messages)

    async def test_stop_listener_returns_cancelled_state(self):
        ws = _FakeWebSocket([])
        bridge = StreamWebSocketBridge(ws, _FakeLogger())
        bridge.cancel_event.set()
        listener = asyncio.create_task(asyncio.sleep(10))

        was_cancelled = await bridge.stop_listener(listener)

        self.assertTrue(was_cancelled)
