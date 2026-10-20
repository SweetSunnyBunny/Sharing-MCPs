'Application module.'

from services.discord_server_map import render_live_map


def _sample():
    return {
        "_refreshed_at": "2026-10-13T23:47:00Z",
        "Claude": {"servers": [{
            "name": "Home Pack", "id": "111",
            "categories": [
                {"category": "Pack Den", "channels": [
                    {"id": "222", "name": "pack-hearth", "type": "text",
                     "topic": "The warm heart of our pack"},
                    {"id": "333", "name": "Lounge", "type": "voice"},
                ]},
                {"category": "Empty", "channels": []},
            ],
        }]},
        "Atlas": {"servers": []},
    }


def test_lists_every_channel_with_its_id():
    md = render_live_map(_sample())
    assert "## Claude" in md
    assert "### Home Pack (guild 111)" in md
    assert "pack-hearth: `222` — The warm heart of our pack" in md
    assert "Lounge [voice]: `333`" in md


def test_skips_empty_categories_and_metadata_keys():
    md = render_live_map(_sample())
    assert "Empty" not in md
    assert "## _refreshed_at" not in md
    assert "Refreshed: 2026-10-13T23:47:00Z" in md


def test_bot_with_no_servers_says_so():
    md = render_live_map(_sample())
    assert "## Atlas\n_No servers visible to this bot._" in md
