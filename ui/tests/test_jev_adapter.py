"""Tests for services/jev_adapter.py — no real network, ever."""

import asyncio
import json

import httpx
import pytest

from services import jev_adapter as jev


def run(coro):
    return asyncio.run(coro)


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


QS = {
    "same_person": jev.noul("Is `a` the same person as `b`?"),
    "kind": jev.choice("Is this a trait or a passing state?", {"trait": None, "state": None}),
    "relevance": jev.score("How relevant is this memory?", ["none", "some", "high"]),
}

GOOD = {
    "model": "jev-1.13.0",
    "answers": {
        "same_person": {"type": "noul", "noul": 0.12},
        "kind": {"type": "choice", "choice": "state",
                 "probabilities": {"trait": 0.2, "state": 0.8}, "confidence": 0.7},
        "relevance": {"type": "score", "score": 1.4, "legend": {"0": "none", "1": "some", "2": "high"},
                      "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5}, "confidence": 0.6},
    },
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.delenv("ANAM_JEV_ALLOW_PRIVATE", raising=False)


def test_disabled_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    called = []
    c = mock_client(lambda r: called.append(r) or httpx.Response(200, json=GOOD))
    assert run(jev.ask("x", QS, synthetic=True, client=c)) is None
    assert called == []


def test_private_refused_before_network(keyed):
    called = []
    c = mock_client(lambda r: called.append(r) or httpx.Response(200, json=GOOD))
    assert run(jev.ask("Owner's real memory", QS, client=c)) is None
    assert called == []


def test_private_allowed_only_by_explicit_flag(keyed, monkeypatch):
    monkeypatch.setenv("ANAM_JEV_ALLOW_PRIVATE", "true")
    c = mock_client(lambda r: httpx.Response(200, json=GOOD))
    assert run(jev.ask("x", QS, client=c)) is not None


def test_request_contract(keyed):
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=GOOD)

    run(jev.ask({"a": 1}, QS, synthetic=True, client=mock_client(handler)))
    assert seen["url"] == jev.JEV_ENDPOINT
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "jev-latest"
    assert seen["body"]["state"] == {"a": 1}
    assert set(seen["body"]["questions"]) == set(QS)


def test_parses_all_three_types(keyed):
    out = run(jev.ask("x", QS, synthetic=True, client=mock_client(lambda r: httpx.Response(200, json=GOOD))))
    assert out["same_person"].value == pytest.approx(0.12)
    assert out["kind"].value == "state" and out["kind"].confidence == pytest.approx(0.7)
    assert out["relevance"].value == pytest.approx(1.4)


@pytest.mark.parametrize("bad", [
    {"type": "noul", "noul": 1.7},
    {"type": "noul", "noul": "yes"},
    {"type": "choice", "noul": 0.5},
    None,
])
def test_malformed_noul_dropped(keyed, bad):
    body = json.loads(json.dumps(GOOD))
    body["answers"]["same_person"] = bad
    out = run(jev.ask("x", QS, synthetic=True, client=mock_client(lambda r: httpx.Response(200, json=body))))
    assert "same_person" not in out and "kind" in out


def test_choice_outside_options_dropped(keyed):
    body = json.loads(json.dumps(GOOD))
    body["answers"]["kind"]["choice"] = "banana"
    out = run(jev.ask("x", QS, synthetic=True, client=mock_client(lambda r: httpx.Response(200, json=body))))
    assert "kind" not in out


def test_score_out_of_range_dropped(keyed):
    body = json.loads(json.dumps(GOOD))
    body["answers"]["relevance"]["score"] = 7
    out = run(jev.ask("x", QS, synthetic=True, client=mock_client(lambda r: httpx.Response(200, json=body))))
    assert "relevance" not in out


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, json={"error": "down"}),
    lambda r: httpx.Response(200, content=b"not json"),
    lambda r: httpx.Response(200, json={"nope": 1}),
])
def test_fails_open(keyed, handler):
    assert run(jev.ask("x", QS, synthetic=True, client=mock_client(handler))) is None


def test_timeout_fails_open(keyed):
    def boom(r):
        raise httpx.ReadTimeout("slow", request=r)
    assert run(jev.ask("x", QS, synthetic=True, client=mock_client(boom))) is None


def test_bad_questions_rejected_without_network(keyed):
    called = []
    c = mock_client(lambda r: called.append(r) or httpx.Response(200, json=GOOD))
    assert run(jev.ask("x", {"q": {"type": "score", "instructions": "x", "criteria": ["only one"]}},
                       synthetic=True, client=c)) is None
    assert called == []
