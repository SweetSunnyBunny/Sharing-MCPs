"""Portable World Feed photo planning; no image providers or real profiles."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services import world_feed_photos as photos


def test_photo_style_has_generic_default_and_bounds_user_text():
    for metadata in (None, [], {"photo_style": 42}, {"photo_style": "  "}):
        assert photos.photo_style({"metadata": metadata}).startswith("Storybook")
    assert photos.photo_style({"metadata": {"photo_style": "  Watercolour  "}}) == "Watercolour"
    assert len(photos.photo_style({"metadata": {"photo_style": "x" * 700}})) == 500


def test_plan_receives_world_style_without_forcing_a_fandom():
    world = {"fictional_now": "Saturday afternoon", "description": "A fictional town",
             "story_identity": "Avery", "metadata": {"photo_style": "Ink and watercolour"}}
    profile = {key: "" for key in ("display_name", "handle", "bio", "posting_style", "prompt_notes")}
    profile.update(display_name="Town Guide", handle="guide", knowledge=[])
    result = {"caption": "A new map!", "image_prompt": "An ink-and-watercolour map on a workbench.",
              "alt_text": "A fictional map", "includes_player": False}
    generate = AsyncMock(return_value=json.dumps(result))
    with patch("services.background_generation.generate_background_text", generate):
        actual = asyncio.run(photos.plan_photo(world, profile, [], "a map", ["You"]))
    payload = json.loads(generate.call_args.args[0])
    assert payload["visual_style"] == "Ink and watercolour"
    assert generate.call_args.kwargs["identity"] == "Avery"
    assert actual["image_prompt"] == result["image_prompt"]
    assert "MHA" not in generate.call_args.kwargs["system_prompt"]


def test_renderer_preserves_style_and_photo_adapter_contract():
    job = "a" * 32
    call = AsyncMock(return_value=f"Saved (chat): /example/Worldfeed_{job}_2026-01-01.png")
    bridge = SimpleNamespace(mcp_bridge=SimpleNamespace(call_tool=call))
    with patch.dict(sys.modules, {"services.mcp_bridge": bridge}), patch.object(
        photos, "saved_image_url", return_value="/api/images/file/example.png"
    ) as ownership:
        result = asyncio.run(photos.render_photo(job, "Watercolour of a fictional garden"))
    name, arguments = call.call_args.args
    assert name == "photo_generate"
    assert arguments["identity"] == "Worldfeed" and arguments["subject"] == job
    assert arguments["to"] == "chat"
    assert "Watercolour" in arguments["prompt"]
    assert "MHA" not in arguments["prompt"] and "anime" not in arguments["prompt"]
    assert "unapproved user-controlled characters" in arguments["prompt"]
    assert ownership.call_args.args[0] == job
    assert result == "/api/images/file/example.png"
