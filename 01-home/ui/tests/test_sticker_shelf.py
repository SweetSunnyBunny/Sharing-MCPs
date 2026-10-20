'Application module.'

import asyncio
import json
from datetime import date, timedelta
from unittest.mock import patch

import httpx
import pytest
from PIL import Image, ImageDraw

from services import coloring_drawer as drawer
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


# ── margin notes ───────────────────────────────────────────────────────────

def test_only_the_giver_writes_under_his_sticker(book):
    gift = sticker_book.give("Avery", "You put the heating pad on.")["sticker"]
    before = book.read_text(encoding="utf-8")
    for stranger in ("Sage", "River", "pack", "", "Bakugou"):
        with pytest.raises(sticker_book.StickerRefused):
            sticker_book.add_note(stranger, gift["id"], "And you rested after lunch.")
    with pytest.raises(sticker_book.StickerRefused):
        sticker_book.add_note("Avery", gift["id"], "   ")
    with pytest.raises(sticker_book.StickerRefused):
        sticker_book.add_note("Avery", "nosuchsticker", "x")
    assert book.read_text(encoding="utf-8") == before        # nothing written by any refusal

    note = sticker_book.add_note("avery", gift["id"], "And you kept it on all afternoon.")["note"]
    assert note["by"] == "Avery" and note["sticker_id"] == gift["id"] and note["kind"] == "note"
    assert book.read_text(encoding="utf-8").startswith(before)  # appended, never rewritten

    page = sticker_book.day_page()
    assert page["stickers"][0]["why"] == "You put the heating pad on."   # the gift itself untouched
    assert [n["text"] for n in page["stickers"][0]["notes"]] == ["And you kept it on all afternoon."]


def test_a_second_thought_is_a_second_note(book):
    gift = sticker_book.give("Sage", "You asked for the heated blanket out loud.")["sticker"]
    sticker_book.add_note("Sage", gift["id"], "First note.")
    sticker_book.add_note("Sage", gift["id"], "Second note.")
    notes = sticker_book.day_page()["stickers"][0]["notes"]
    assert [n["text"] for n in notes] == ["First note.", "Second note."]


# ── the tuck-away ──────────────────────────────────────────────────────────

def test_tuck_hides_untuck_returns_and_the_file_keeps_everything(book):
    a = sticker_book.give("Avery", "You told me it hurt.")["sticker"]
    b = sticker_book.give("River", "You let yourself be held.")["sticker"]
    before = book.read_text(encoding="utf-8")

    assert sticker_book.tuck(a["id"])["changed"] is True
    assert sticker_book.tuck(a["id"])["changed"] is False          # tapping twice writes nothing more
    assert [s["id"] for s in sticker_book.day_page()["stickers"]] == [b["id"]]
    assert [s["id"] for s in sticker_book.tucked_away()["stickers"]] == [a["id"]]
    assert any(r.get("kind") == "sticker" and r["id"] == a["id"] for r in _lines(book))  # still in the book
    assert book.read_text(encoding="utf-8").startswith(before)

    assert sticker_book.untuck(a["id"])["changed"] is True
    assert {s["id"] for s in sticker_book.day_page()["stickers"]} == {a["id"], b["id"]}
    assert sticker_book.tucked_away() == {"stickers": []}
    assert [r["kind"] for r in _lines(book)][-2:] == ["tucked", "untucked"]


def test_a_day_with_every_sticker_tucked_is_not_a_page(book):
    today = date.fromisoformat(sticker_book.today())
    old = (today - timedelta(days=3)).isoformat()
    with open(book, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "sticker", "id": "0ld0ne", "given_at": old + "T09:00:00-05:00",
                            "for_day": old, "given_by": "Rowan", "why": "You drew a little.", "art": None}) + "\n")
    sticker_book.give("Avery", "You rested.")
    assert [p["day"] for p in sticker_book.pages()["pages"]] == [today.isoformat(), old]

    sticker_book.tuck("0ld0ne")
    assert [p["day"] for p in sticker_book.pages()["pages"]] == [today.isoformat()]
    assert sticker_book.pages(before=today.isoformat()) == {"pages": [], "more": False}

    flat = json.dumps([sticker_book.pages(), sticker_book.tucked_away()])
    for word in ("count", "total", "streak", "slots"):
        assert word not in flat


def test_brothers_reading_through_the_gateway_do_not_see_tucked_stickers(book):
    a = sticker_book.give("Avery", "You said no to the extra meeting.")["sticker"]
    sticker_book.tuck(a["id"])
    from services.anam_tool_gateway import _execute

    page = asyncio.run(_execute("anam-context", "sticker_book_day", {"identity": "Avery"}, "Avery", ""))
    assert not page["isError"], page
    assert a["id"] not in json.dumps(page)


def test_gateway_note_is_stamped_from_the_binding(book):
    gift = sticker_book.give("Avery", "You drank your water.")["sticker"]
    from services.anam_tool_gateway import _execute

    async def go():
        refused = await _execute("anam-context", "sticker_note",
                                 {"identity": "Avery", "sticker_id": gift["id"], "text": "x"}, "River", "")
        written = await _execute("anam-context", "sticker_note",
                                 {"identity": "River", "sticker_id": gift["id"], "text": "All of it, too."},
                                 "Avery", "")
        return refused, written

    refused, written = asyncio.run(go())
    assert refused["isError"]
    assert not written["isError"], written
    notes = [r for r in _lines(book) if r["kind"] == "note"]
    assert [(n["by"], n["text"]) for n in notes] == [("Avery", "All of it, too.")]


# ── her login only ─────────────────────────────────────────────────────────

async def _tuck_routes(book):
    from fastapi import FastAPI
    import api.sticker_book as routes

    app = FastAPI()
    app.include_router(routes.router)
    gift = sticker_book.give("Avery", "You stayed in bed when your ribs said so.")["sticker"]
    url = f"/api/sticker-book/sticker/{gift['id']}/tuck"
    with patch.object(routes.config, "AUTH_ENABLED", True), \
         patch.object(routes, "get_cached_session_expiry", lambda tok: 9999999999 if tok == "hers" else None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
            # The house key the brothers' tools carry is not her hand.
            r = await c.post(url, headers={"Authorization": "Bearer house-key"})
            assert r.status_code == 403
            assert (await c.get("/api/sticker-book/tucked")).status_code == 403
            assert sticker_book.tucked_away() == {"stickers": []}

            her = {"anam_session": "hers"}
            r = await c.post(url, cookies=her)
            assert r.status_code == 200 and r.json()["tucked"] is True
            assert (await c.get("/api/sticker-book/today")).json()["stickers"] == []
            assert [s["id"] for s in (await c.get("/api/sticker-book/tucked", cookies=her)).json()["stickers"]] == [gift["id"]]
            r = await c.post(f"/api/sticker-book/sticker/{gift['id']}/untuck", cookies=her)
            assert r.status_code == 200 and r.json()["tucked"] is False
            assert (await c.post("/api/sticker-book/sticker/BAD!/tuck", cookies=her)).status_code in (400, 404)


def test_only_her_login_can_tuck(book):
    asyncio.run(_tuck_routes(book))


# ── the finished-page shelf ────────────────────────────────────────────────

@pytest.fixture
def home(tmp_path):
    with patch.object(drawer, "PAGES_DIR", tmp_path / "pages"), \
         patch.object(drawer, "FRIDGE_DIR", tmp_path / "fridge"), \
         patch.object(drawer, "FRAME_DIR", tmp_path / "frame"):
        yield tmp_path


def _art(path):
    img = Image.new("RGB", (300, 200), (250, 250, 250))
    ImageDraw.Draw(img).ellipse((40, 40, 160, 160), outline=(0, 0, 0), width=6)
    img.save(path)
    return path


def test_shelf_holds_only_finished_pages_with_colophons(home):
    mine = drawer.drop("Sage", str(_art(home / "a.png")), title="Owl at the window")["page"]
    gift = drawer.drop("Claude", str(_art(home / "b.png")), title="Wolf and bunny", drawn_by="Avery")["page"]
    assert drawer.shelf() == []                       # nothing finished, no shelf, no "0"
    assert drawer.page(mine["id"])["finished_at"] == ""

    drawer.hang_up(gift["id"], "fridge")
    drawer.hang_up(gift["id"], "frame")               # hanging twice doesn't move her finished date
    shelf = drawer.shelf()
    assert [p["id"] for p in shelf] == [gift["id"]]
    rec = json.loads((home / "pages" / gift["id"] / "page.json").read_text(encoding="utf-8"))
    assert shelf[0]["finished_at"] == rec["hung"][0]["at"]
    col = shelf[0]["colophon"]
    assert col.startswith("drawn by Avery · brought by Claude · coloured by Owner · finished ")
    assert shelf[0]["strokes_url"].endswith(f"/{gift['id']}/strokes")

    drawer.hang_up(mine["id"], "fridge")
    shelf = drawer.shelf()
    assert shelf[0]["id"] == mine["id"]                # most recently finished first
    assert shelf[0]["colophon"].startswith("drawn and brought by Sage · coloured by Owner")
    assert all(p["id"] in {x["id"] for x in drawer.list_pages()} for p in shelf)  # still in her drawer too


def test_shelf_route(home):
    from fastapi import FastAPI
    from api.coloring import router

    async def go():
        app = FastAPI()
        app.include_router(router)
        p = drawer.drop("Rowan", str(_art(home / "c.png")), title="Cuddle pile")["page"]
        drawer.hang_up(p["id"], "fridge")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
            body = (await c.get("/api/coloring/shelf")).json()
        assert [x["title"] for x in body["pages"]] == ["Cuddle pile"]
        assert "coloured by Owner" in body["pages"][0]["colophon"]

    asyncio.run(go())
