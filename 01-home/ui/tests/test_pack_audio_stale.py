'Application module.'
import json
import os
import time

import config
from services import trigger_engine


def _setup(tmp_path, monkeypatch, queue_len, age_days):
    voice = tmp_path / "voice"
    voice.mkdir()
    f = voice / "queued-abc.mp3"
    f.write_bytes(b"x")
    old = time.time() - age_days * 86400
    os.utime(f, (old, old))
    (tmp_path / "voice_queue.json").write_text(
        json.dumps([{"message_id": f"queued-{i}"} for i in range(queue_len)]), encoding="utf-8"
    )
    monkeypatch.setattr(config, "VOICE_DIR", voice)


def test_quiet_when_drawer_full_of_unheard(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, queue_len=7, age_days=40)
    assert trigger_engine._eval_pack_audio_stale({"days": 5}) is False


def test_still_nudges_when_drawer_nearly_empty(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, queue_len=1, age_days=40)
    assert trigger_engine._eval_pack_audio_stale({"days": 5}) is True


def test_fresh_note_not_stale(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, queue_len=0, age_days=1)
    assert trigger_engine._eval_pack_audio_stale({"days": 5}) is False


def test_max_unheard_is_tunable(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, queue_len=4, age_days=40)
    assert trigger_engine._eval_pack_audio_stale({"days": 5, "max_unheard": 10}) is True
