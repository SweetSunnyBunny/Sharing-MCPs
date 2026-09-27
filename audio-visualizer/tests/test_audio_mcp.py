"""Offline checks: subprocesses, network transcription and media processing are mocked."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_audio", ROOT / "audio_mcp_server.py")
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        for target, value in [("OUTPUT_ROOT", self.root / "output"), ("URL_CACHE", self.root / "cache")]:
            p = patch.object(audio, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def media(self, name="sample.mp4"):
        path = self.root / name
        path.write_bytes(b"offline fixture")
        return path

    def download(self, command, **kwargs):
        template = command[command.index("-o") + 1]
        dest = Path(template.replace("%(title).60s", "sample").replace("%(ext)s", "mp4"))
        dest.write_bytes(b"offline fixture")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_ffmpeg(self, command, **kwargs):
        if "-show_format" in command:
            return subprocess.CompletedProcess(command, 0, '{"format":{"duration":"12"}}', "")
        Path(command[-1]).write_bytes(b"x" * 2000)
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_nine_registered_tools_and_original_signatures(self):
        tools = asyncio.run(audio.mcp.get_tools())
        self.assertEqual(set(tools), {"audio_info", "audio_tempo_key", "audio_visualize", "audio_analyze", "audio_review", "video_watch", "watch_video", "video_see", "video_listen"})
        self.assertEqual(tools["video_watch"].parameters["required"], ["path"])
        self.assertEqual(tools["video_watch"].parameters["properties"]["identity"]["default"], "default")

    def test_modes_dispatch_without_media_calls(self):
        with patch.object(audio, "_watch_impl", return_value="mocked") as call:
            self.assertEqual(audio.video_watch.fn("local.mp4", 4), "mocked")
            call.assert_called_with("local.mp4", 4, identity="default")
            audio.watch_video.fn("https://example.com/v", 8)
            call.assert_called_with("https://example.com/v", 8, identity="default")
            audio.video_see.fn("local.mp4")
            call.assert_called_with("local.mp4", 8, want_transcript=False, identity="default")
            audio.video_listen.fn("local.mp4")
            call.assert_called_with("local.mp4", want_frames=False, identity="default")

    def test_local_path_needs_no_browser_or_downloader(self):
        media = self.media()
        with patch.object(audio, "_fetch_url", side_effect=AssertionError("unexpected URL")):
            self.assertEqual(audio._resolve_media(str(media)), (media, ""))

    def test_url_validation(self):
        for url in ["file:///x", "https://", "https://[broken", "https://user:password@example.com/video"]:
            with self.subTest(url=url), patch.object(audio.subprocess, "run") as run:
                self.assertIsNone(audio._fetch_url(url)[0])
                run.assert_not_called()

    def test_default_never_reads_browser_profile(self):
        self.assertIsNone(audio._profile_for("default"))
        with self.assertRaises(ValueError):
            audio._profile_for("research")

    def test_explicit_profile_file_and_unknown_identity(self):
        profile = self.root / "browser"
        profile.mkdir()
        mapping = self.root / "profiles.json"
        mapping.write_text(json.dumps({"research": str(profile)}))
        with patch.dict(os.environ, {"AUDIO_CHROME_PROFILES_FILE": str(mapping)}):
            self.assertEqual(audio._profile_for("research"), profile)
            self.assertIsNone(audio._profile_for("default"))
            with self.assertRaises(ValueError):
                audio._profile_for("other")
            with self.assertRaises(ValueError):
                audio._profile_for("../research")

    def test_profile_requires_existing_absolute_directory(self):
        mapping = self.root / "profiles.json"
        mapping.write_text('{"research":"relative/profile"}')
        with patch.dict(os.environ, {"AUDIO_CHROME_PROFILES_FILE": str(mapping)}):
            with self.assertRaises(ValueError):
                audio._profile_for("research")

    def test_url_download_no_cookies_and_ignores_user_config(self):
        with patch.object(audio, "_ytdlp", return_value=["yt-dlp"]), patch.object(audio.subprocess, "run", side_effect=self.download) as run:
            result, note = audio._fetch_url("https://example.com/video")
            self.assertTrue(result.is_file())
            self.assertIn("no browser cookies", note)
            command = run.call_args.args[0]
            self.assertIn("--ignore-config", command)
            self.assertNotIn("--cookies-from-browser", command)
            self.assertEqual(command[-2:], ["--", "https://example.com/video"])

    def test_audio_only_download_flags(self):
        with patch.object(audio, "_ytdlp", return_value=["yt-dlp"]), patch.object(audio.subprocess, "run", side_effect=self.download) as run:
            audio._fetch_url("https://example.com/video", audio_only=True)
            command = run.call_args.args[0]
            self.assertIn("-x", command)
            self.assertIn("--audio-format", command)
            self.assertNotIn("--merge-output-format", command)

    def test_explicit_browser_failure_retries_without_cookies(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                return subprocess.CompletedProcess(command, 1, "", "browser unavailable")
            return self.download(command, **kwargs)
        with patch.object(audio, "_profile_for", return_value=self.root), patch.object(audio, "_ytdlp", return_value=["yt-dlp"]), patch.object(audio.subprocess, "run", side_effect=run):
            self.assertIsNotNone(audio._fetch_url("https://example.com/video", "research")[0])
        self.assertIn("--cookies-from-browser", calls[0])
        self.assertNotIn("--cookies-from-browser", calls[1])

    def test_download_cache_and_profile_scope(self):
        with patch.object(audio, "_profile_for", return_value=None), patch.object(audio, "_ytdlp", return_value=["yt-dlp"]), patch.object(audio.subprocess, "run", side_effect=self.download) as run:
            first = audio._fetch_url("https://example.com/video", "one")[0]
            self.assertEqual(audio._fetch_url("https://example.com/video", "one")[0], first)
            second = audio._fetch_url("https://example.com/video", "two")[0]
            self.assertNotEqual(first, second)
            self.assertEqual(run.call_count, 2)

    def test_partial_files_not_cache_hits_and_failure_cleanup_is_scoped(self):
        audio.URL_CACHE.mkdir()
        key = audio._url_key("https://example.com/video", False, "default|no-cookies")
        partial = audio.URL_CACHE / f"{key}--sample.mp4.part"
        partial.write_bytes(b"partial")
        other = audio.URL_CACHE / "another-key--sample.mp4.part"
        other.write_bytes(b"keep")
        with patch.object(audio, "_ytdlp", return_value=["yt-dlp"]), patch.object(audio.subprocess, "run", side_effect=subprocess.TimeoutExpired("mock", 300)):
            self.assertIsNone(audio._fetch_url("https://example.com/video")[0])
        self.assertFalse(partial.exists())
        self.assertTrue(other.exists())

    def test_missing_downloader_has_actionable_error(self):
        with patch.object(audio, "_ytdlp", return_value=None):
            self.assertIn("requirements", audio._fetch_url("https://example.com/video")[1])

    def test_no_key_is_status_not_fabricated_transcript(self):
        with patch.object(audio.shutil, "which", return_value="mock"), patch.object(audio.subprocess, "run", side_effect=self.fake_ffmpeg), patch.object(audio, "_transcribe_audio", new_callable=AsyncMock) as transcribe:
            result = audio._watch_impl(str(self.media()), 4)
        self.assertIn("CONTACT SHEET", result)
        self.assertIn("GROQ_API_KEY", result)
        self.assertNotIn("AUDIO TRANSCRIPT", result)
        transcribe.assert_not_called()

    def test_cache_without_transcript_retries_after_key_added(self):
        media = self.media()
        with patch.object(audio.shutil, "which", return_value="mock"), patch.object(audio.subprocess, "run", side_effect=self.fake_ffmpeg), patch.object(audio, "_transcribe_audio", new_callable=AsyncMock, return_value="Test transcript") as transcribe:
            audio._watch_impl(str(media), 4)
            with patch.dict(os.environ, {"GROQ_API_KEY": "test-placeholder"}):
                self.assertIn("Test transcript", audio._watch_impl(str(media), 4))
                self.assertIn("served instantly", audio._watch_impl(str(media), 4))
        self.assertEqual(transcribe.await_count, 1)

    def test_transient_transcription_failure_retries(self):
        media = self.media()
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-placeholder"}), patch.object(audio.shutil, "which", return_value="mock"), patch.object(audio.subprocess, "run", side_effect=self.fake_ffmpeg), patch.object(audio, "_transcribe_audio", new_callable=AsyncMock, side_effect=[RuntimeError("private error contents"), "Recovered transcript"]) as transcribe:
            first = audio._watch_impl(str(media), 4)
            second = audio._watch_impl(str(media), 4)
        self.assertNotIn("private error contents", first)
        self.assertNotIn("AUDIO TRANSCRIPT", first)
        self.assertIn("Recovered transcript", second)
        self.assertEqual(transcribe.await_count, 2)

    def test_frames_only_skips_transcription(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-placeholder"}), patch.object(audio.shutil, "which", return_value="mock"), patch.object(audio.subprocess, "run", side_effect=self.fake_ffmpeg), patch.object(audio, "_transcribe_audio", new_callable=AsyncMock) as transcribe:
            self.assertIn("CONTACT SHEET", audio._watch_impl(str(self.media()), 4, want_transcript=False))
        transcribe.assert_not_called()

    def test_listen_without_key_does_not_claim_heard_audio(self):
        with patch.object(audio.shutil, "which", return_value="mock"), patch.object(audio.subprocess, "run", side_effect=self.fake_ffmpeg):
            result = audio._watch_impl(str(self.media()), want_frames=False)
        self.assertIn("No frames or transcript", result)
        self.assertNotIn("actually HEARD", result)

    def test_async_bridge_inside_running_loop(self):
        async def value():
            return "done"
        async def outer():
            return audio._run_async(value())
        self.assertEqual(asyncio.run(outer()), "done")

    def test_http_binds_only_loopback_and_stdio_remains_default(self):
        with patch.object(audio.mcp, "run") as run:
            audio.main([])
            run.assert_called_with(transport="stdio")
            audio.main(["--transport", "http", "--port", "8820"])
            run.assert_called_with(transport="http", host="127.0.0.1", port=8820)


if __name__ == "__main__":
    unittest.main()
