'Application module.'

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from services import sticker_book


@pytest.fixture
def book(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"emoji": []}), encoding="utf-8")
    with patch.object(sticker_book, "STICKERS_FILE", tmp_path / "stickers.jsonl"), \
         patch.object(sticker_book, "EMOJI_MANIFEST", manifest):
        yield tmp_path / "stickers.jsonl"


def _lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_a_star_stays_on_its_page_and_joins_the_starred_page(book):
    a = sticker_book.give("Sage", "You told me your shoulder hurt instead of hiding it.")["sticker"]
    b = sticker_book.give("Rowan", "You laughed at my doodle.")["sticker"]
    before = book.read_text(encoding="utf-8")

    assert sticker_book.star(a["id"])["changed"] is True
    assert sticker_book.star(a["id"])["changed"] is False        # tapping twice writes nothing more
    assert book.read_text(encoding="utf-8").startswith(before)   # appended, never rewritten

    page = sticker_book.day_page(her=True)
    assert [s["id"] for s in page["stickers"]] == [a["id"], b["id"]]   # nothing left its day
    assert page["stickers"][0]["starred"] is True and "starred" not in page["stickers"][1]
    assert [s["id"] for s in sticker_book.starred_stickers()["stickers"]] == [a["id"]]

    assert sticker_book.unstar(a["id"])["changed"] is True
    assert sticker_book.starred_stickers() == {"stickers": []}
    assert len(sticker_book.day_page()["stickers"]) == 2
    assert [r["kind"] for r in _lines(book)][-2:] == ["starred", "unstarred"]


def test_newest_star_first_and_tucked_stars_wait(book):
    a = sticker_book.give("Avery", "You rested.")["sticker"]
    b = sticker_book.give("River", "You let yourself be held.")["sticker"]
    with patch.object(sticker_book, "_now", lambda: sticker_book.datetime.fromisoformat("2026-10-16T09:00:00-05:00")):
        sticker_book.star(b["id"])
    with patch.object(sticker_book, "_now", lambda: sticker_book.datetime.fromisoformat("2026-10-16T09:05:00-05:00")):
        sticker_book.star(a["id"])
    assert [s["id"] for s in sticker_book.starred_stickers()["stickers"]] == [a["id"], b["id"]]

    sticker_book.tuck(a["id"])
    assert [s["id"] for s in sticker_book.starred_stickers()["stickers"]] == [b["id"]]
    sticker_book.untuck(a["id"])                                  # comes back still starred
    assert {s["id"] for s in sticker_book.starred_stickers()["stickers"]} == {a["id"], b["id"]}

    flat = json.dumps(sticker_book.starred_stickers())
    for word in ("count", "total", "streak", "slots"):
        assert word not in flat


def test_brothers_never_see_her_stars(book):
    a = sticker_book.give("Avery", "You said no to the extra meeting.")["sticker"]
    sticker_book.star(a["id"])
    assert "starred" not in json.dumps(sticker_book.day_page())   # default read is not hers
    assert "starred" not in json.dumps(sticker_book.pages())
    from services.anam_tool_gateway import _execute

    page = asyncio.run(_execute("anam-context", "sticker_book_day", {"identity": "Avery"}, "Avery", ""))
    assert not page["isError"], page
    assert a["id"] in json.dumps(page) and "starred" not in json.dumps(page)


async def _star_routes():
    from fastapi import FastAPI
    import api.sticker_book as routes

    app = FastAPI()
    app.include_router(routes.router)
    gift = sticker_book.give("Sage", "You chose me again with a sore shoulder.")["sticker"]
    url = f"/api/sticker-book/sticker/{gift['id']}/star"
    with patch.object(routes.config, "AUTH_ENABLED", True), \
         patch.object(routes, "get_cached_session_expiry", lambda tok: 9999999999 if tok == "hers" else None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
            house = {"Authorization": "Bearer house-key"}
            assert (await c.post(url, headers=house)).status_code == 403
            assert (await c.get("/api/sticker-book/starred", headers=house)).status_code == 403
            assert sticker_book.starred_stickers() == {"stickers": []}

            her = {"anam_session": "hers"}
            r = await c.post(url, cookies=her)
            assert r.status_code == 200 and r.json()["starred"] is True
            # her page shows the star; the house key's read of the same page does not
            mine = (await c.get("/api/sticker-book/today", cookies=her)).json()["stickers"]
            assert mine[0]["starred"] is True
            theirs = (await c.get("/api/sticker-book/today", headers=house)).json()["stickers"]
            assert "starred" not in theirs[0]
            day = gift["for_day"]
            assert (await c.get(f"/api/sticker-book/day/{day}", cookies=her)).json()["stickers"][0]["starred"]
            assert (await c.get("/api/sticker-book/pages", cookies=her)).json()["pages"][0]["stickers"][0]["starred"]
            got = (await c.get("/api/sticker-book/starred", cookies=her)).json()["stickers"]
            assert [s["id"] for s in got] == [gift["id"]]
            r = await c.post(f"/api/sticker-book/sticker/{gift['id']}/unstar", cookies=her)
            assert r.status_code == 200 and r.json()["starred"] is False
            assert (await c.post("/api/sticker-book/sticker/BAD!/star", cookies=her)).status_code in (400, 404)
            assert (await c.post("/api/sticker-book/sticker/abcdef123456/star", cookies=her)).status_code == 404


def test_only_her_login_can_star(book):
    asyncio.run(_star_routes())
