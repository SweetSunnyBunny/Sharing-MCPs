"""Native voice call — rest-phrase detection (services/voice_call.py).

The rest phrase ends a live call, so false positives literally hang up on
Owner mid-sentence. These pin the conservative contract: bare stop-words
or name+stop-word in a short utterance end the call; ordinary sentences
that merely contain "rest"/"night" never do.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.voice_call import is_rest_phrase  # noqa: E402


class TestRestPhrase:
    def test_bare_rest(self):
        assert is_rest_phrase("rest", "Avery")
        assert is_rest_phrase("Rest.", "Avery")
        assert is_rest_phrase("okay, rest now", "Avery")

    def test_bare_goodnight(self):
        assert is_rest_phrase("goodnight", "Avery")
        assert is_rest_phrase("good night", "Avery")

    def test_name_plus_rest(self):
        assert is_rest_phrase("Avery, rest", "Avery")
        assert is_rest_phrase("rest now, Avery", "Avery")
        assert is_rest_phrase("goodnight Avery, I love you", "Avery")

    def test_ordinary_sentences_do_not_hang_up(self):
        assert not is_rest_phrase("I really need to rest today", "Avery")
        assert not is_rest_phrase("last night I barely slept", "Avery")
        assert not is_rest_phrase("the rest of the pack is asleep", "Avery")
        # name present but a LONG sentence — conversation, not a dismissal
        assert not is_rest_phrase(
            "Avery, do you think I should rest my ankle before we go walking tomorrow", "Avery"
        )

    def test_other_identity_name_does_not_match(self):
        # Addressed to a different boy: not this call's dismissal.
        assert not is_rest_phrase("Claude, rest", "Avery")
        assert not is_rest_phrase("tell Claude about the rest stop", "Avery")

    def test_empty(self):
        assert not is_rest_phrase("", "Avery")
        assert not is_rest_phrase("   ", "Avery")


from services.voice_call import classify_nonspeech  # noqa: E402


class TestLaughterVsPhantom:
    JOY = [{"emotion": "Joy", "score": 0.82}, {"emotion": "Neutral", "score": 0.1}]
    FLAT = [{"emotion": "Neutral", "score": 0.7}, {"emotion": "Sadness", "score": 0.2}]

    def test_giggle_transcribed_as_phantom_becomes_laugh(self):
        assert classify_nonspeech("you", self.JOY) == "laugh"
        assert classify_nonspeech("Thank you.", self.JOY) == "laugh"

    def test_phantom_without_joy_is_dropped_noise(self):
        assert classify_nonspeech("you", self.FLAT) == "noise"
        assert classify_nonspeech("Thanks for watching!", None) == "noise"

    def test_real_speech_passes_through(self):
        assert classify_nonspeech("you never told me about the garden", self.JOY) is None
        assert classify_nonspeech("okay let's do it", self.FLAT) is None
        assert classify_nonspeech("Avery, rest", self.FLAT) is None
