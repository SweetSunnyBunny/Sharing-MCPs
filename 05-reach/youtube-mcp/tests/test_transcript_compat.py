"""Network-free regression for current and legacy transcript library adapters."""
import ast
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from typing import Any, Dict


def load_transcript_function(api_class, cache_dir):
    source = Path(__file__).resolve().parents[1] / "server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in {"_fetch_transcript_segments", "get_transcript"}]
    for node in nodes:
        node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    namespace = {
        "YouTubeTranscriptApi": api_class,
        "TRANSCRIPT_AVAILABLE": True,
        "CACHE_DIR": cache_dir,
        "extract_video_id": lambda value: value,
        "Field": lambda default, **kwargs: default,
        "Dict": Dict, "Any": Any, "json": json,
    }
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["get_transcript"]


class TranscriptCompatibilityTests(unittest.TestCase):
    def test_current_fetch_returns_text_timestamps_and_cache(self):
        segments = [{"text": "Hello", "start": 0.0, "duration": 1.0},
                    {"text": "world", "start": 1.0, "duration": 1.5}]
        calls = []

        class Fetched:
            def to_raw_data(self):
                return segments

        class CurrentApi:
            def fetch(self, video_id):
                calls.append(video_id)
                return Fetched()

        with tempfile.TemporaryDirectory() as directory:
            get_transcript = load_transcript_function(CurrentApi, Path(directory))
            result = asyncio.run(get_transcript("sample-id", False))
            self.assertTrue(result["success"])
            self.assertEqual(result["transcript"], "Hello world")
            self.assertEqual(result["segment_count"], 2)
            timed = asyncio.run(get_transcript("sample-id", True))
            self.assertEqual(timed["transcript"], segments)
            self.assertEqual(calls, ["sample-id"])

    def test_legacy_class_method_remains_supported(self):
        class LegacyApi:
            @staticmethod
            def get_transcript(video_id):
                return [{"text": "Legacy example", "start": 0.0, "duration": 2.0}]

        with tempfile.TemporaryDirectory() as directory:
            get_transcript = load_transcript_function(LegacyApi, Path(directory))
            result = asyncio.run(get_transcript("legacy-id", False))
            self.assertTrue(result["success"])
            self.assertEqual(result["transcript"], "Legacy example")


if __name__ == "__main__":
    unittest.main()
