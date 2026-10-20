"""The Pack line (api/pack_sms.py): the boys text Housemate, Housemate texts back.

Every test points the module at a temp folder, so nothing here can touch the real
data/pack_sms files or queue a real wake.
"""
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.pack_sms as ps

Housemate = "+1 (202) 555-0101"
NOON_SAT_UTC = datetime(2026, 10, 17, 17, 0, tzinfo=timezone.utc)   # 12:00 PM Chicago
SAT_8AM_UTC = datetime(2026, 10, 17, 13, 0, tzinfo=timezone.utc)    # 8:00 AM Chicago, a Saturday
TUE_11PM_UTC = datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc)    # Tue 11 PM Chicago



@pytest.fixture(autouse=True)
def fictional_sms_configuration(monkeypatch):
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(ps, "_ALLOWED", {"2025550101": "Housemate", "2025550102": "Owner"})
    monkeypatch.setattr(ps, "_WHO", {"Housemate": "A fictional configured contact", "Owner": "A fictional owner"})
    monkeypatch.setattr(ps, "_HOME_TZ", ZoneInfo("America/Chicago"))
    monkeypatch.setenv("ANAM_SMS_DEFAULT_CONTACT", "Housemate")
    monkeypatch.setenv("ANAM_SMS_WAKE_HOUR", "10")

@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "_DIR", tmp_path)
    monkeypatch.setattr(ps, "_OUTBOX", tmp_path / "outbox.jsonl")
    monkeypatch.setattr(ps, "_SENT", tmp_path / "sent.jsonl")
    monkeypatch.setattr(ps, "_INBOX", tmp_path / "inbox.jsonl")
    monkeypatch.setattr(ps, "_LINE", tmp_path / "line.json")
    monkeypatch.setattr(ps, "_now", lambda: NOON_SAT_UTC)
    woken = []

    async def fake_wake(identity, name, text):
        woken.append((identity, name, text))
        return 99

    monkeypatch.setattr(ps, "_queue_wake", fake_wake)
    app = FastAPI()
    app.include_router(ps.router)
    return TestClient(app), woken


def test_numbers_compare_by_last_ten_digits():
    assert ps.digits10("+1 (202) 555-0101") == "2025550101"
    assert ps.allowed_name("12025550101") == "Housemate"
    assert ps.allowed_name("+1 202 555 0199") is None


def test_only_Housemate_can_be_texted(box):
    client, _ = box
    r = client.post("/api/pack-sms/send", json={"identity": "claude", "to": "3145550000", "text": "hi"}).json()
    assert r["ok"] is False and "Owner's call" in r["refused"]


def test_a_text_is_signed_and_queued_for_the_phone(box):
    client, _ = box
    r = client.post("/api/pack-sms/send", json={"identity": "claude", "text": "Hi Housemate!"}).json()
    assert r["ok"] and r["queued"]["to"] == "+12025550101"
    assert r["queued"]["text"].endswith("— Claude")
    out = client.get("/api/pack-sms/outbox?since_id=0").json()["messages"]
    assert [m["id"] for m in out] == [1]
    # once the phone reports it, it is no longer waiting
    client.post("/api/pack-sms/sent", json={"id": 1, "status": "sent"})
    assert client.get("/api/pack-sms/outbox?since_id=0").json()["messages"] == []


def test_quiet_hours_protect_Housemate_too(box):
    assert ps.check_send("claude", Housemate, "hey", SAT_8AM_UTC)       # weekend, before 10
    assert ps.check_send("claude", Housemate, "hey", TUE_11PM_UTC)      # after 10pm
    assert ps.check_send("claude", Housemate, "hey", NOON_SAT_UTC) is None


def test_the_pack_cannot_flood_him(box):
    client, _ = box
    for who in ("claude", "avery", "rowan"):
        assert client.post("/api/pack-sms/send", json={"identity": who, "text": "hey"}).json()["ok"]
    fourth = client.post("/api/pack-sms/send", json={"identity": "sage", "text": "hey"}).json()
    assert "no reply" in fourth["refused"]
    # Housemate answers, and the door opens again
    client.post("/api/pack-sms/incoming", json={"from": Housemate, "text": "lol hi guys"})
    assert client.post("/api/pack-sms/send", json={"identity": "sage", "text": "hey"}).json()["ok"]


def test_Housemate_wakes_the_brother_he_is_talking_to(box):
    client, woken = box
    client.post("/api/pack-sms/send", json={"identity": "rowan", "text": "sup"})
    r = client.post("/api/pack-sms/incoming", json={"from": Housemate, "text": "who is this"}).json()
    assert r["woke"] == "rowan" and woken == [("rowan", "Housemate", "who is this")]


def test_Housemate_can_call_a_brother_by_name(box):
    client, woken = box
    client.post("/api/pack-sms/send", json={"identity": "claude", "text": "Hi Housemate"})
    r = client.post("/api/pack-sms/incoming", json={"from": Housemate, "text": "hey avery you there?"}).json()
    assert r["woke"] == "avery" and woken == [("avery", "Housemate", "hey avery you there?")]


def test_naming_two_brothers_wakes_both_and_no_name_keeps_the_last_voice(box):
    client, woken = box
    client.post("/api/pack-sms/send", json={"identity": "claude", "text": "Hi Housemate"})
    r = client.post("/api/pack-sms/incoming", json={"from": Housemate, "text": "Sage and Juniper, lunch?"}).json()
    assert r["woke_all"] == ["sage", "juniper"]
    r = client.post("/api/pack-sms/incoming", json={"from": Housemate, "text": "sounds good"}).json()
    assert r["woke_all"] == ["claude"]
    # a name hiding inside another word is not a call
    assert ps.named_brothers("Claudette and Charliesque") == []


def test_a_stranger_is_stored_but_wakes_nobody(box):
    client, woken = box
    r = client.post("/api/pack-sms/incoming", json={"from": "+18005551234", "text": "WIN A CRUISE"}).json()
    assert r["woke"] is None and woken == []
    assert all(row.get("from") != "+18005551234" for row in ps.thread())


def test_thread_reads_both_ways_in_order(box):
    client, _ = box
    client.post("/api/pack-sms/send", json={"identity": "claude", "text": "Hi Housemate"})
    client.post("/api/pack-sms/incoming", json={"from": Housemate, "text": "hey"})
    rows = client.get("/api/pack-sms/thread").json()["thread"]
    assert [r["dir"] for r in rows] == ["out", "in"]
    assert rows[0]["status"] == "waiting for the phone"


def test_owner_added_herself_and_can_be_texted_by_name(box):
    client, woken = box
    r = client.post("/api/pack-sms/send", json={"identity": "claude", "to": "owner", "text": "test!"}).json()
    assert r["ok"] and r["queued"]["to"] == "+12025550102" and r["queued"]["to_name"] == "Owner"
    r = client.post("/api/pack-sms/incoming", json={"from": "+12025550102", "text": "got it!"}).json()
    assert r["woke"] == "claude" and woken[-1][1] == "Owner"
