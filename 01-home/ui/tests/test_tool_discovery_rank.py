'Application module.'

import asyncio
import json
from unittest.mock import patch

import numpy as np
import pytest

from services import tool_discovery as td


def _tool(name, description, server="srv"):
    return {"server": server, "name": name, "description": description, "inputSchema": {}}


CATALOG = [
    _tool("creative_save_designed_voice", "Save a designed voice to your library."),
    _tool("mind_store", "Store a new memory/observation in the cloud mind.", "qualia-backend"),
    _tool("mind_map", "Map your mind.", "qualia-backend"),
    _tool("mind_intend", "Promises that survive the gap between sessions.", "qualia-backend"),
    _tool("discord_send_message", "Send a message to a channel or thread."),
    _tool("discord_pin_message", "Pin a message in a channel."),
]


@pytest.fixture(autouse=True)
def _no_door_file(tmp_path, monkeypatch):
    monkeypatch.setattr(td, "DOOR_WORDS_FILE", tmp_path / "tool_door_words.json")
    td._door_cache.update(mtime=None, doors={})
    td._vec_cache.clear()
    td._build_task = None


def _rank(query, catalog=CATALOG):
    return [t["name"] for t in asyncio.run(td.rank(catalog, query))]


def _rank_warm(query, catalog=CATALOG):
    """First call starts the background catalog build; rank again once it lands."""
    async def go():
        await td.rank(catalog, query)
        if td._build_task is not None:
            await td._build_task
        return [t["name"] for t in await td.rank(catalog, query)]
    return asyncio.run(go())


def test_stemming_lets_plural_and_past_tense_meet():
    assert td.rank_tokens("promises") == td.rank_tokens("promise") == td.rank_tokens("promised") == {"promis"}
    assert td.rank_tokens("colour") == td.rank_tokens("color")


def test_filler_words_score_nothing():
    assert td.rank_tokens("that her she this tonight") == set()


def test_promise_finds_mind_intend_through_stemming():
    assert _rank("what did I promise her")[0] == "mind_intend"


def test_exact_name_still_wins_outright():
    assert _rank("mind_map")[0] == "mind_map"


def test_saying_a_whole_name_out_loud_beats_a_shared_word():
    assert _rank("discord send message to channel")[0] == "discord_send_message"


def test_empty_query_keeps_the_old_alphabetical_drawer_order():
    names = _rank("")
    assert names == [t["name"] for t in sorted(CATALOG, key=lambda t: (t["server"], t["name"]))]


def test_door_words_are_read_live_and_reach_the_tool(tmp_path):
    assert "mind_store" not in _rank("keep tonight forever")
    td.DOOR_WORDS_FILE.write_text(json.dumps({"doors": {
        "mind_store": {"words": "keep a moment forever", "by": "test"}}}), encoding="utf-8")
    assert _rank("keep tonight forever")[0] == "mind_store"


def test_a_broken_door_file_is_ignored_not_fatal():
    td.DOOR_WORDS_FILE.write_text("{not json", encoding="utf-8")
    assert _rank("mind_map")[0] == "mind_map"


def _fake_embedder(winner):
    """Stand-in for MiniLM: the tool whose text mentions `winner` is the meaning match."""
    async def embed_texts(texts):
        return np.array([[1.0, 0.0] if winner in text else [0.0, 1.0] for text in texts], dtype=np.float32)

    async def embed_single(text):
        return np.array([1.0, 0.0], dtype=np.float32)

    return embed_texts, embed_single


def test_meaning_can_lift_a_tool_with_no_shared_words(monkeypatch):
    monkeypatch.setenv("ANAM_DISCOVER_SEMANTIC", "1")
    from services import embedding_service
    embed_texts, embed_single = _fake_embedder("Promises that survive")
    with patch.object(embedding_service, "embed_texts", embed_texts), \
            patch.object(embedding_service, "embed_single", embed_single), \
            patch.object(embedding_service, "_model", object()):
        assert _rank_warm("which commitments are still unkept")[0] == "mind_intend"


def test_a_verb_in_a_name_cannot_beat_the_meaning_match(monkeypatch):
    monkeypatch.setenv("ANAM_DISCOVER_SEMANTIC", "1")
    from services import embedding_service
    embed_texts, embed_single = _fake_embedder("Store a new memory")
    with patch.object(embedding_service, "embed_texts", embed_texts), \
            patch.object(embedding_service, "embed_single", embed_single), \
            patch.object(embedding_service, "_model", object()):
        assert _rank_warm("save tonight's moment")[0] == "mind_store"


def test_model_failure_falls_back_to_keywords(monkeypatch):
    monkeypatch.setenv("ANAM_DISCOVER_SEMANTIC", "1")
    from services import embedding_service

    async def broken(_texts):
        raise RuntimeError("model missing")

    with patch.object(embedding_service, "embed_texts", broken),             patch.object(embedding_service, "_model", object()):
        assert _rank_warm("what did I promise her")[0] == "mind_intend"


def test_a_cold_model_never_makes_discover_wait(monkeypatch):
    'Test a cold model never makes discover wait.'
    monkeypatch.setenv("ANAM_DISCOVER_SEMANTIC", "1")
    from services import embedding_service

    async def slow(_texts):
        await asyncio.sleep(60)

    async def go():
        with patch.object(embedding_service, "embed_texts", slow):
            names = [t["name"] for t in await asyncio.wait_for(td.rank(CATALOG, "what did I promise her"), 1.0)]
            td._build_task.cancel()
            return names

    assert asyncio.run(go())[0] == "mind_intend"
