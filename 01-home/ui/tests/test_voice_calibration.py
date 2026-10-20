import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiosqlite

from db.schema import init_db
from services import voice_calibration


class VoiceCalibrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.audio_dir = Path(self.tempdir.name) / "audio"
        self.audio_dir.mkdir()
        self.audio_id = "a1b2c3d4e5f6"
        self.audio_path = self.audio_dir / f"{self.audio_id}.webm"
        self.audio_path.write_bytes(b"not-real-audio")
        self.audio_path.with_suffix(".webm.txt").write_text(
            "I am soft, not angry", encoding="utf-8"
        )
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await init_db(self.db)
        self.audio_patch = patch.object(voice_calibration, "AUDIO_DIR", self.audio_dir)
        self.ext_patch = patch.object(
            voice_calibration, "AUDIO_ALLOWED_EXTENSIONS", {".webm"}
        )
        self.audio_patch.start()
        self.ext_patch.start()

    async def asyncTearDown(self):
        self.ext_patch.stop()
        self.audio_patch.stop()
        await self.db.close()
        self.tempdir.cleanup()

    async def test_label_round_trip_keeps_transcript_and_shadow_prediction(self):
        voice_calibration.save_shadow_prediction(
            self.audio_path,
            [{"emotion": "Anger", "score": 0.87}],
            model_id="old-four-box-ear",
        )

        state = await voice_calibration.save_calibration(
            self.db,
            audio_id=self.audio_id,
            label="sad_soft",
            target_identity="Avery",
            conversation_id="conv-1",
        )

        self.assertEqual(state["selected"], "sad_soft")
        self.assertEqual(
            state["shadow_prediction"]["predictions"][0]["emotion"], "Anger"
        )
        rows = await self.db.execute_fetchall(
            "SELECT label, transcript, prediction_json FROM voice_calibration_samples"
        )
        self.assertEqual(rows[0]["label"], "sad_soft")
        self.assertEqual(rows[0]["transcript"], "I am soft, not angry")
        self.assertEqual(
            json.loads(rows[0]["prediction_json"])["model_id"],
            "old-four-box-ear",
        )

    async def test_relabel_updates_one_sample_instead_of_duplicating(self):
        await voice_calibration.save_calibration(
            self.db, audio_id=self.audio_id, label="tired"
        )
        await voice_calibration.save_calibration(
            self.db, audio_id=self.audio_id, label="soft_clingy"
        )

        stats = await voice_calibration.calibration_stats(self.db)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["counts"], {"soft_clingy": 1})

    async def test_unknown_label_is_refused(self):
        with self.assertRaises(ValueError):
            await voice_calibration.save_calibration(
                self.db, audio_id=self.audio_id, label="furious_apparently"
            )

    def test_audio_id_cannot_be_a_filename_or_traversal(self):
        self.assertIsNone(voice_calibration.resolve_audio_path("../secrets"))
        self.assertIsNone(voice_calibration.resolve_audio_path("clip.webm"))
        self.assertEqual(
            voice_calibration.resolve_audio_path(self.audio_id), self.audio_path
        )

    def test_calibration_options_are_copies(self):
        first = voice_calibration.calibration_options()
        first[0]["label"] = "changed"
        second = voice_calibration.calibration_options()
        self.assertEqual(second[0]["label"], "sad / soft")

    def test_live_call_sample_keeps_audio_transcript_and_blind_options(self):
        state = voice_calibration.save_live_call_sample(
            b"voice-from-the-wolfs-ear",
            "audio/webm;codecs=opus",
            "You even like my squeaks?",
            [{"emotion": "Joy", "score": 0.71}],
            model_id="test-ear",
        )

        audio_path = voice_calibration.resolve_audio_path(state["audio_id"])
        self.assertIsNotNone(audio_path)
        self.assertEqual(audio_path.read_bytes(), b"voice-from-the-wolfs-ear")
        self.assertEqual(
            audio_path.with_suffix(".webm.txt").read_text(encoding="utf-8"),
            "You even like my squeaks?",
        )
        self.assertEqual(
            voice_calibration.load_shadow_prediction(audio_path)["predictions"][0]["emotion"],
            "Joy",
        )
        self.assertNotIn("shadow_prediction", state)
        self.assertIn(
            "happy_joyful", {option["key"] for option in state["options"]}
        )


if __name__ == "__main__":
    unittest.main()
