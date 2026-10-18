'Application module.'

import io
import json
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from services import coloring_drawer as drawer


@pytest.fixture
def home(tmp_path):
    with patch.object(drawer, "PAGES_DIR", tmp_path / "pages"), \
         patch.object(drawer, "FRIDGE_DIR", tmp_path / "fridge"), \
         patch.object(drawer, "FRAME_DIR", tmp_path / "frame"), \
         patch.object(drawer, "PALETTE_PATHS", (tmp_path / "nope.json",)):
        yield tmp_path


def _line_art(path, gap=False, grey=245):
    """A circle and a square in black on off-white paper (like generated pages are)."""
    img = Image.new("RGB", (400, 300), (grey, grey, grey))
    d = ImageDraw.Draw(img)
    d.ellipse((40, 40, 180, 180), outline=(0, 0, 0), width=6)
    if gap:
        d.line((220, 40, 360, 40), fill=(0, 0, 0), width=6)       # a square missing its sides
    else:
        d.rectangle((220, 40, 360, 180), outline=(0, 0, 0), width=6)
    img.save(path)
    return path


def test_drop_stamps_the_dropper_and_makes_clear_lines(home):
    src = _line_art(home / "page.png")
    out = drawer.drop("avery", str(src), title="Wolf and bunny", drawn_by="Avery", tool="image-gen")
    page = out["page"]
    assert page["dropped_by"] == "Avery" and page["drawn_by"] == "Avery"
    d = home / "pages" / page["id"]
    rec = json.loads((d / "page.json").read_text(encoding="utf-8"))
    assert rec["tool"] == "image-gen" and rec["original"] == "original.png"
    assert (d / "original.png").read_bytes() == src.read_bytes()   # the original is never changed
    lines = Image.open(d / "lines.png")
    assert lines.mode == "RGBA"
    # Off-white paper (245) is fully clear: no milky veil over her colour.
    assert lines.getpixel((5, 5))[3] == 0
    # The ink is solid black, so the lines stay inky above her paint.
    r, g, b, a = lines.getpixel((110, 43))
    assert (r, g, b) == (0, 0, 0) and a == 255


def test_levels_curve_ramps_between_ink_and_paper():
    img = Image.new("L", (3, 1))
    img.putpixel((0, 0), drawer.INK_AT)
    img.putpixel((1, 0), (drawer.INK_AT + drawer.CLEAR_AT) // 2)
    img.putpixel((2, 0), drawer.CLEAR_AT)
    out = drawer.make_lines(img.convert("RGB"))
    alphas = [out.getpixel((x, 0))[3] for x in range(3)]
    assert alphas[0] == 255 and alphas[2] == 0 and 100 < alphas[1] < 155


@pytest.mark.parametrize("who", ["", "pack", "Bakugou", "owner", "nobody"])
def test_no_real_dropper_means_nothing_in_the_drawer(home, who):
    src = _line_art(home / "page.png")
    with pytest.raises(drawer.DropRefused):
        drawer.drop(who, str(src))
    assert not (home / "pages").exists() or not any((home / "pages").iterdir())


def test_missing_or_broken_picture_is_refused(home):
    with pytest.raises(drawer.DropRefused):
        drawer.drop("Claude", str(home / "not-there.png"))
    bad = home / "bad.png"
    bad.write_bytes(b"not a picture")
    with pytest.raises(drawer.DropRefused):
        drawer.drop("Claude", str(bad))


def test_gap_check_informs_but_never_blocks(home):
    closed = drawer.drop("Rowan", str(_line_art(home / "closed.png")))
    leaky = drawer.drop("Rowan", str(_line_art(home / "leaky.png", gap=True)))
    assert closed["gap_check"]["regions"] > leaky["gap_check"]["regions"]
    # The leaky page is still in the drawer; the check only tells the dropper.
    assert len(drawer.list_pages()) == 2


def test_her_paint_and_strokes_are_kept_against_the_page(home):
    page = drawer.drop("Claude", str(_line_art(home / "p.png")))["page"]
    paint = Image.new("RGBA", (page["width"], page["height"]), (0, 0, 0, 0))
    ImageDraw.Draw(paint).ellipse((60, 60, 160, 160), fill=(244, 166, 188, 230))
    buf = io.BytesIO(); paint.save(buf, "PNG")
    stroke = {"type": "stroke", "t": 1200, "tool": "brush", "color": "#F4A6BC", "pts": [[60, 60, 0, 18]]}
    out = drawer.save_progress(page["id"], buf.getvalue(), [stroke, {"type": "undo", "t": 1900}, {"type": "evil"}])
    assert out["saved_events"] == 2
    drawer.save_progress(page["id"], None, [{"type": "redo", "t": 2500}])
    events = drawer.strokes(page["id"])
    assert [e["type"] for e in events] == ["stroke", "undo", "redo"]   # append-only, undo is an event
    assert drawer.page(page["id"])["started"] is True
    # The lines were never touched by her paint.
    d = home / "pages" / page["id"]
    assert Image.open(d / "lines.png").getpixel((110, 110))[3] == 0


def test_wrong_sized_or_fake_paint_is_refused(home):
    page = drawer.drop("Claude", str(_line_art(home / "p.png")))["page"]
    small = io.BytesIO(); Image.new("RGBA", (10, 10)).save(small, "PNG")
    with pytest.raises(ValueError):
        drawer.save_progress(page["id"], small.getvalue(), [])
    with pytest.raises(ValueError):
        drawer.save_progress(page["id"], b"nope", [])


def test_hang_up_is_the_only_road_out(home):
    page = drawer.drop("Claude", str(_line_art(home / "p.png")))["page"]
    assert not (home / "fridge").exists()
    paint = Image.new("RGBA", (page["width"], page["height"]), (0, 0, 0, 0))
    ImageDraw.Draw(paint).rectangle((0, 0, 400, 300), fill=(141, 184, 224, 255))
    buf = io.BytesIO(); paint.save(buf, "PNG")
    drawer.save_progress(page["id"], buf.getvalue(), [])
    out = drawer.hang_up(page["id"], "fridge")
    hung = Image.open(out["file"]).convert("RGB")
    assert hung.getpixel((5, 5)) == (141, 184, 224)       # her colour under ...
    assert hung.getpixel((110, 43)) == (0, 0, 0)          # ... the lines on top
    assert drawer.page(page["id"])["hung"][0]["where"] == "fridge"
    with pytest.raises(ValueError):
        drawer.hang_up(page["id"], "discord")


def test_page_ids_cannot_walk_out_of_the_drawer(home):
    for bad in ("../wellness", "..", "a/b", "", "X"):
        with pytest.raises(drawer.PageNotFound):
            drawer.page(bad)
    page = drawer.drop("Claude", str(_line_art(home / "p.png")))["page"]
    with pytest.raises(drawer.PageNotFound):
        drawer.file_path(page["id"], "page.json")


def test_rebuild_lines_keeps_her_paint(home):
    page = drawer.drop("Claude", str(_line_art(home / "p.png")))["page"]
    buf = io.BytesIO(); Image.new("RGBA", (page["width"], page["height"]), (1, 2, 3, 255)).save(buf, "PNG")
    drawer.save_progress(page["id"], buf.getvalue(), [])
    drawer.rebuild_lines(page["id"])
    d = home / "pages" / page["id"]
    assert Image.open(d / "paint.png").getpixel((0, 0)) == (1, 2, 3, 255)


def test_palette_falls_back_when_charlies_box_is_missing(home):
    assert drawer.palette()["rows"]


def test_routes_read_and_save_behind_the_api(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.coloring import router

    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    page = drawer.drop("Claude", str(_line_art(home / "p.png")), title="Test page")["page"]
    assert client.get("/api/coloring/pages").json()["pages"][0]["id"] == page["id"]
    assert client.get(f"/api/coloring/page/{page['id']}/lines.png").status_code == 200
    assert client.get(f"/api/coloring/page/{page['id']}/paint.png").status_code == 404
    buf = io.BytesIO(); Image.new("RGBA", (page["width"], page["height"])).save(buf, "PNG")
    r = client.post(f"/api/coloring/page/{page['id']}/save",
                    files={"paint": ("paint.png", buf.getvalue(), "image/png")},
                    data={"events": json.dumps([{"type": "stroke", "t": 1, "pts": []}])})
    assert r.status_code == 200 and r.json()["saved_events"] == 1
    assert client.get(f"/api/coloring/page/{page['id']}/paint.png").status_code == 200
    assert client.get("/api/coloring/page/nope-nope/lines.png").status_code == 404
    assert client.post(f"/api/coloring/page/{page['id']}/hang", json={"where": "x"}).status_code == 400
    # There is no drop route: an HTTP body could name anyone.
    assert client.post("/api/coloring/drop", json={}).status_code in (404, 405)


def _png(size, box=None):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if box:
        ImageDraw.Draw(img).rectangle(box, fill=(250, 200, 60, 230))
    buf = io.BytesIO(); img.save(buf, "PNG")
    return buf.getvalue()


def test_a_blank_save_over_her_paint_keeps_what_it_replaced(home):
    'Test a blank save over her paint keeps what it replaced.'
    page = drawer.drop("Claude", str(_line_art(home / "p.png")))["page"]
    size = (page["width"], page["height"])
    d = home / "pages" / page["id"]
    drawer.save_progress(page["id"], _png(size, (20, 20, 300, 250)), [])
    drawer.save_progress(page["id"], _png(size, (20, 20, 290, 250)), [])   # a small erase: no copy
    assert not (d / "paint_history").exists()
    drawer.save_progress(page["id"], _png(size), [])                        # suddenly blank
    kept = list((d / "paint_history").glob("paint-*.png"))
    assert len(kept) == 1
    assert drawer._painted(kept[0]) > 50000                                  # her colour is safe
    assert drawer._painted(d / "paint.png") == 0                            # her save still landed
