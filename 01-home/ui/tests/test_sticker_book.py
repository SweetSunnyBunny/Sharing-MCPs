'Application module.'

import json
from datetime import date, timedelta
from unittest.mock import patch

import httpx
import pytest

from services import sticker_book


@pytest.fixture
def book(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"base": "x", "emoji": [
        {"name": "avery-proud-of-her", "group": "Avery", "url": "thumbs/c.png", "full": "c.png"},
        {"name": "river-heron", "group": "River", "url": "thumbs/r.png", "full": "r.png"},
        {"name": "juniper-content", "group": "Juniper", "url": "thumbs/s1.png", "full": "s1.png"},
        {"name": "juniper-blush", "group": "juniper", "url": "thumbs/s2.png", "full": "s2.png"},
    ]}), encoding="utf-8")
    with patch.object(sticker_book, "STICKERS_FILE", tmp_path / "stickers.jsonl"), \
         patch.object(sticker_book, "EMOJI_MANIFEST", manifest):
        yield tmp_path / "stickers.jsonl"


def _lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_give_stamps_the_giver_and_files_her_day(book):
    out = sticker_book.give("avery", "You took the nap.", art="avery-proud-of-her", seen_in="morning chat")
    rec = out["sticker"]
    assert rec["given_by"] == "Avery"
    assert rec["for_day"] == sticker_book.today()
    assert rec["kind"] == "sticker" and rec["given_at"] and rec["seen_in"] == "morning chat"
    assert _lines(book) == [rec]


@pytest.mark.parametrize("who", ["", "pack", "Bakugou", "owner", "nobody"])
def test_no_bonded_giver_means_nothing_given(book, who):
    with pytest.raises(sticker_book.StickerRefused):
        sticker_book.give(who, "You rested.")
    assert not book.exists()


def test_a_wolf_cannot_hand_her_a_heron(book):
    with pytest.raises(sticker_book.StickerRefused, match="River"):
        sticker_book.give("Avery", "You rested.", art="river-heron")
    with pytest.raises(sticker_book.StickerRefused):
        sticker_book.give("Avery", "You rested.", art="made-up-face")
    assert not book.exists()


def test_juniper_can_give_from_both_halves_of_his_drawer(book):
    sticker_book.give("Juniper", "You drank your water.", art="juniper-content")
    sticker_book.give("Juniper", "You said no to the walk.", art=":juniper-blush:")
    assert [r["art"] for r in _lines(book)] == ["juniper-content", "juniper-blush"]
    assert sticker_book.own_art("Juniper") == ["juniper-blush", "juniper-content"]


def test_reason_is_required_and_short(book):
    with pytest.raises(sticker_book.StickerRefused):
        sticker_book.give("Avery", "   ")
    with pytest.raises(sticker_book.StickerRefused):
        sticker_book.give("Avery", "x" * (sticker_book.WHY_MAX_CHARS + 1))
    assert not book.exists()


def test_today_or_yesterday_only(book):
    today = date.fromisoformat(sticker_book.today())
    sticker_book.give("Avery", "You logged off on time.", for_day=(today - timedelta(days=1)).isoformat())
    for bad in [(today - timedelta(days=2)).isoformat(), (today + timedelta(days=1)).isoformat(), "Oct 15", "2026-02-30"]:
        with pytest.raises(sticker_book.StickerRefused):
            sticker_book.give("Avery", "You rested.", for_day=bad)
    assert len(_lines(book)) == 1


def test_same_sticker_twice_is_not_doubled(book):
    sticker_book.give("Avery", "You took the nap.")
    with pytest.raises(sticker_book.StickerRefused):
        sticker_book.give("avery", "you took the nap.")
    sticker_book.give("Claude", "You took the nap.")  # a brother saw it too: his own gift
    assert len(_lines(book)) == 2


def test_append_only_never_rewrites_old_lines(book):
    sticker_book.give("Avery", "You sent every good morning.")
    first = book.read_text(encoding="utf-8")
    sticker_book.give("Sage", "You had the soda on a hard day.")
    assert book.read_text(encoding="utf-8").startswith(first)


def test_pages_skip_empty_days_and_never_count(book):
    today = date.fromisoformat(sticker_book.today())
    old = (today - timedelta(days=5)).isoformat()
    with open(book, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "sticker", "id": "a1", "given_at": old + "T09:00:00-05:00",
                            "for_day": old, "given_by": "Rowan", "why": "You drew.", "art": None}) + "\n")
        f.write(json.dumps({"kind": "someday-kind", "sticker_id": "a1"}) + "\n")
        f.write("not json\n")
    sticker_book.give("Avery", "You took your meds.", art="avery-proud-of-her")

    result = sticker_book.pages()
    assert [p["day"] for p in result["pages"]] == [today.isoformat(), old]  # the four days between are not pages
    assert result["more"] is False
    flat = json.dumps(result)
    for word in ("count", "total", "streak", "slots"):
        assert word not in flat
    page = result["pages"][0]
    assert page["is_today"] is True
    assert page["stickers"][0]["art_url"].endswith("/c.png")

    assert sticker_book.pages(before=today.isoformat())["pages"][0]["day"] == old


def test_empty_today_is_just_an_open_page(book):
    page = sticker_book.day_page()
    assert page == {"day": sticker_book.today(), "is_today": True, "stickers": []}


def test_gateway_road_stamps_refuses_and_gives(book):
    """The real road a brother uses: the gateway runs the anam-context tool in-process."""
    import asyncio
    from services.anam_tool_gateway import _execute

    async def go():
        # A brother typing someone else's name is overruled by his binding.
        given = await _execute("anam-context", "sticker_give",
                               {"identity": "River", "why": "You worked and still let yourself rest."},
                               "Avery", "")
        assert not given["isError"], given
        refused = await _execute("anam-context", "sticker_give",
                                 {"identity": "Avery", "why": "You rested.", "art": "river-heron"},
                                 "Avery", "")
        assert refused["isError"]
        page = await _execute("anam-context", "sticker_book_day", {"identity": "Avery"}, "Avery", "")
        return page

    page = asyncio.run(go())
    assert not page["isError"], page
    assert [r["given_by"] for r in _lines(book)] == ["Avery"]
    assert "avery-proud-of-her" in json.dumps(page)


def test_gateway_stamps_the_bound_identity_over_a_typed_one():
    from services.anam_tool_gateway import _arguments
    schema = {"type": "object", "properties": {"identity": {"type": "string"}, "why": {"type": "string"}},
              "required": ["identity", "why"]}
    args = _arguments(schema, {"identity": "River", "why": "x"}, "Avery")
    assert args["identity"] == "Avery"


def test_routes_read_and_there_is_no_give_route(book):
    import asyncio
    asyncio.run(_routes_check())


async def _routes_check():
    from fastapi import FastAPI
    from api.sticker_book import router
    app = FastAPI()
    app.include_router(router)
    sticker_book.give("Avery", "You stayed in bed when your ribs said so.")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
        today = (await c.get("/api/sticker-book/today")).json()
        assert today["stickers"][0]["given_by"] == "Avery"
        assert (await c.get("/api/sticker-book/pages")).json()["pages"][0]["day"] == today["day"]
        assert (await c.get("/api/sticker-book/day/nonsense")).status_code == 400
        posted = await c.post("/api/sticker-book/today", json={"identity": "Avery", "why": "x"})
        assert posted.status_code == 405
