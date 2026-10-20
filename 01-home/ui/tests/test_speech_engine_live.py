import unittest
from unittest.mock import patch

from services.speech_engine_live import (
    SpeechTextChunker,
    clean_live_speech,
    get_live_call_status,
)


class SpeechTextChunkerTests(unittest.TestCase):
    def test_streams_complete_sentences_and_flushes_tail(self):
        chunker = SpeechTextChunker()

        self.assertEqual(chunker.feed("Hello there. How are"), ["Hello there. "])
        self.assertEqual(chunker.feed(" you?"), [])
        self.assertEqual(chunker.flush(), ["How are you? "])

    def test_strips_nonspoken_control_blocks_and_markdown(self):
        text = (
            "**Hello**, Owner. <react>heart</react> "
            "<canvas title=\"notes\">do not read this</canvas> "
            "<voice>[softly] I am here.</voice>"
        )

        spoken = clean_live_speech(text)

        self.assertIn("Hello, Owner.", spoken)
        self.assertIn("[softly] I am here.", spoken)
        self.assertNotIn("heart", spoken)
        self.assertNotIn("do not read", spoken)

    def test_long_phrase_splits_at_a_word_boundary(self):
        chunker = SpeechTextChunker(max_chars=80)
        chunks = chunker.feed("word " * 30)

        self.assertGreaterEqual(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 85 for chunk in chunks))

    def test_never_speaks_a_control_block_split_across_deltas(self):
        chunker = SpeechTextChunker()

        self.assertEqual(chunker.feed('<canvas title="notes">Secret sentence. '), [])
        self.assertEqual(
            chunker.feed('</canvas>Safe sentence. '),
            ['Safe sentence. '],
        )


class LiveCallStatusTests(unittest.TestCase):
    def test_status_never_exposes_engine_ids_or_api_key(self):
        fake_config = {
            "api_key": "secret",
            "speech_engines": {"Avery": "seng_secret_id"},
        }
        with patch(
            "services.speech_engine_live._load_private_config",
            return_value=fake_config,
        ):
            status = get_live_call_status()

        self.assertTrue(status["available"])
        self.assertEqual(status["configured_identities"], ["Avery"])
        self.assertNotIn("secret", str(status).lower())
        self.assertNotIn("seng_", str(status))


if __name__ == "__main__":
    unittest.main()
