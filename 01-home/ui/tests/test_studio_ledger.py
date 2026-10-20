"The studio ledger's live door: stamped, append-only, and never eaten by a backfill re-run."

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from services import coloring_drawer as drawer
from services import studio_ledger as ledger


@pytest.fixture
def book(tmp_path):
    with patch.object(ledger, "LEDGER", tmp_path / "studio" / "ledger.jsonl"):
        yield tmp_path


def _page(path, shade=0):
    img = Image.new("RGB", (120, 90), (250, 250, 250))
    ImageDraw.Draw(img).ellipse((10, 10, 80, 80), outline=(shade, shade, shade), width=4)
    img.save(path)
    return path


def _rows(tmp_path):
    p = tmp_path / "studio" / "ledger.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_row_carries_the_stamp_the_claim_and_the_road(book):
    src = _page(book / "original.png")
    out = ledger.append_drop(path=str(src), dropped_by="juniper", drawn_by="rowan",
                             tool="Krita", source="a moth on a crescent, lines only")
    assert out["written"] is True
    [row] = _rows(book)
    assert row["dropped_by"] == "Juniper"            # stamped, canonical case
    assert row["drawn_by"] == "Rowan" and row["drawn_by_is"] == "claim"
    assert row["tool"] == "krita" and row["road"] == "coloring-drawer"
    assert row["provenance"] == "given-to" and row["from"] == "juniper" and row["to"] == "owner"
    assert row["prompt_or_source"] == "a moth on a crescent, lines only"
    assert row["name_on_file"] is None            # a filename is never evidence
    assert row["source"] == "coloring-drawer"     # not "backfill", so re-runs keep it
    import hashlib
    assert row["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()


def test_no_claim_means_no_claim(book):
    src = _page(book / "original.png")
    row = ledger.append_drop(path=str(src), dropped_by="Avery")["row"]
    assert row["drawn_by"] == "" and row["drawn_by_is"] == ""


@pytest.mark.parametrize("who", ["", "pack", "Bakugou", "owner"])
def test_not_one_of_the_eight_writes_nothing(book, who):
    src = _page(book / "original.png")
    with pytest.raises(ledger.LedgerRefused):
        ledger.append_drop(path=str(src), dropped_by=who)
    assert not (book / "studio" / "ledger.jsonl").exists()


def test_missing_file_writes_nothing(book):
    with pytest.raises(ledger.LedgerRefused):
        ledger.append_drop(path=str(book / "nope.png"), dropped_by="juniper")
    assert not (book / "studio" / "ledger.jsonl").exists()


def test_unknown_tool_is_other_not_invented(book):
    src = _page(book / "original.png")
    assert ledger.append_drop(path=str(src), dropped_by="juniper", tool="crayons")["row"]["tool"] == "other"


def test_same_file_same_bytes_is_one_row(book):
    src = _page(book / "original.png")
    ledger.append_drop(path=str(src), dropped_by="juniper")
    again = ledger.append_drop(path=str(src), dropped_by="juniper")
    assert again["written"] is False
    assert len(_rows(book)) == 1


def test_append_only_old_lines_are_never_touched(book):
    led = book / "studio" / "ledger.jsonl"
    led.parent.mkdir(parents=True)
    old = ('{"file": "data/images/x.png", "sha256": "abc", "source": "backfill", "provenance": "unknown"}\n'
           'not json, but not mine to fix\n')
    led.write_text(old, encoding="utf-8")
    ledger.append_drop(path=str(_page(book / "original.png")), dropped_by="juniper")
    assert led.read_text(encoding="utf-8").startswith(old)


def test_byte_twins_are_named(book):
    a = _page(book / "a.png")
    b = book / "b" / "original.png"
    b.parent.mkdir()
    b.write_bytes(a.read_bytes())
    ledger.append_drop(path=str(a), dropped_by="juniper")
    row = ledger.append_drop(path=str(b), dropped_by="claude")["row"]
    assert row["byte_twins"] == [_rows(book)[0]["file"]]


def test_a_drawer_drop_writes_exactly_one_ledger_row(book):
    src = _page(book / "lines.png")
    with patch.object(drawer, "PAGES_DIR", book / "pages"):
        out = drawer.drop("river", str(src), title="Heron", drawn_by="River", tool="anidoodle")
    [row] = _rows(book)
    assert row["dropped_by"] == "River" and row["tool"] == "anidoodle"
    assert row["evidence"][0]["page_id"] == out["page"]["id"]
    assert row["file"].endswith(f"{out['page']['id']}/original.png")


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / "scripts/studio_ledger_backfill.py").exists(), reason="Private archive migration is not distributed")
def test_backfill_rerun_carries_live_rows_forward(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "studio_ledger_backfill", Path(__file__).resolve().parents[1] / "scripts" / "studio_ledger_backfill.py")
    backfill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backfill)

    led = tmp_path / "ledger.jsonl"
    live = '{"file": "data/coloring/pages/p-1/original.png", "source": "coloring-drawer"}'
    led.write_text('{"file": "data/images/x.png", "source": "backfill"}\n'
                   + live + "\n" + "garbled line\n", encoding="utf-8")
    assert backfill.carry_forward(led) == [live, "garbled line"]
    assert backfill.carry_forward(tmp_path / "missing.jsonl") == []
