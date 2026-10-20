import asyncio
import base64
from io import BytesIO
import json
import os
import sys
import time
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from PIL import Image
import pytest

from services import anam_media as media, connector_health as health, interactive_terminal as terminal


@pytest.fixture
def picture(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "CACHE", tmp_path / "cache")
    path = tmp_path / "original.png"
    Image.new("RGB", (800, 600), "red").save(path)
    return path


def test_preview_cache_crop_and_original_are_preserved(picture):
    original = picture.read_bytes()
    first = media.preview(str(picture), max_dimension=256)
    second = media.preview(str(picture), max_dimension=256)
    assert first == second
    assert first["preview_dimensions"] == [256, 192]
    crop = media.preview(media_id=first["media_id"], crop=[0, 0, 200, 300])
    assert crop["preview_dimensions"] == [200, 300]
    assert crop["dimensions"] == [800, 600]
    assert picture.read_bytes() == original
    assert media.attachment_path(first["media_id"]).is_file()


def test_media_validates_source_change_bounds_and_ids(picture):
    first = media.preview(str(picture))
    with pytest.raises(ValueError):
        media.preview(media_id=first["media_id"], crop=[0, 0, 900, 10])
    with pytest.raises(ValueError):
        media.attachment_path("../../other")
    Image.new("RGB", (100, 100), "blue").save(picture)
    with pytest.raises(ValueError, match="source changed"):
        media.preview(media_id=first["media_id"])


def test_native_image_receipt_prepares_real_attachment(picture):
    data = base64.b64encode(picture.read_bytes()).decode()
    records = media.cache_result_images({"content": [{"type": "image", "mimeType": "image/png", "data": data}]})
    assert len(records) == 1
    assert "data" not in records[0]
    with Image.open(media.attachment_path(records[0]["media_id"])) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)


def test_native_mcp_preview_returns_pixels(picture):
    result = media.native_preview(str(picture))
    assert result.content[0].type == "text"
    assert result.content[1].type == "image"
    assert "media_id" in json.loads(result.content[0].text)


def test_preview_strips_metadata_and_expires(picture, monkeypatch):
    with Image.open(picture) as image:
        exif = image.getexif()
        exif[270] = "private source note"
        image.save(picture, exif=exif)
    record = media.preview(str(picture))
    with Image.open(media.attachment_path(record["media_id"])) as image:
        assert not image.getexif()
        assert "exif" not in image.info
    now = media.time.time()
    monkeypatch.setattr(media.time, "time", lambda: now + media.TTL + 1)
    with pytest.raises(ValueError, match="expired"):
        media.attachment_path(record["media_id"])


@pytest.mark.parametrize("error,expected", [("HTTP 401 secret-token", "auth_required"),
    ("HTTP 403", "permission_denied"), ("HTTP 429", "rate_limited"),
    ("Unknown tool", "missing_tool"), ("Client session closed", "stale_connection"),
    ("Invalid arguments at x", "invalid_arguments")])
def test_connector_error_classification_is_redacted(error, expected):
    health.observe("test", error=RuntimeError(error))
    assert health._events["test"]["status"] == expected
    assert error not in json.dumps(health._events["test"])
    health.observe("test")
    assert health._events["test"]["last_failure"]["status"] == expected


def test_terminal_api_requires_machine_auth(monkeypatch):
    from api.computer_tools import router
    app = FastAPI(); app.include_router(router)
    monkeypatch.setenv("ANAM_API_KEY", "test")
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.post("/api/computer/terminal", json={"operation":"start"})).status_code == 401
            assert (await client.post("/api/computer/terminal", headers={"Authorization":"Bearer test"}, json={"operation":"unknown"})).status_code == 400
    asyncio.run(run())


@pytest.mark.skipif(os.name != "nt", reason="Windows ConPTY")
def test_interactive_input_output_and_process_exit(tmp_path):
    command = '"' + sys.executable + '" -u -c "print(\'READY\',flush=True); print(\'GOT=\'+input(),flush=True)"'
    result = terminal.start(command, str(tmp_path), "cmd")
    sid = result["session_id"]
    output = result["output"]
    cursor = result["next_cursor"]
    try:
        terminal.write(sid, "sunflower", enter=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = terminal.read(sid, cursor, wait_ms=500)
            output += result["output"]; cursor = result["next_cursor"]
            if result["output_complete"]:
                break
        assert "GOT=sunflower" in output
        assert result["status"] == "exited", repr(terminal._sessions[sid]["output"])
        assert result["exit_code"] == 0
        assert terminal.read(sid, cursor)["output"] == ""
    finally:
        terminal.close(sid)


@pytest.mark.skipif(os.name != "nt", reason="Windows ConPTY")
def test_ctrl_c_reaches_the_process(tmp_path):
    command = '"' + sys.executable + '" -u -c "import time; print(\'READY\',flush=True); time.sleep(60)"'
    result = terminal.start(command, str(tmp_path), "cmd")
    sid = result["session_id"]
    try:
        deadline = time.monotonic() + 10
        while "READY" not in result["output"] and time.monotonic() < deadline:
            result = terminal.read(sid, wait_ms=500)
            time.sleep(.05)
        assert "READY" in result["output"]
        assert terminal.interrupt(sid)["interrupt_sent"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = terminal.read(sid, wait_ms=100)
            if result["status"] == "exited":
                break
            time.sleep(.05)
        assert result["status"] == "exited", repr(terminal._sessions[sid]["output"])
    finally:
        terminal.close(sid)


@pytest.mark.skipif(os.name != "nt", reason="Windows ConPTY")
def test_powershell_preserves_quotes_and_unicode(tmp_path):
    result = terminal.start("[Console]::WriteLine('Owner says \"héllo\"')", str(tmp_path))
    sid = result["session_id"]
    output, cursor = result["output"], result["next_cursor"]
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = terminal.read(sid, cursor, wait_ms=500)
            output += result["output"]; cursor = result["next_cursor"]
            if result["output_complete"]:
                break
        assert 'Owner says "héllo"' in output
        assert result["exit_code"] == 0
    finally:
        terminal.close(sid)
