"""Owner's context card (#19) — "me right now", one card read by all six.

GET/POST /api/hub/context-card (patch-style save, always refreshes
updated_at) and the orientation-side honesty grammar: silent when empty,
silent past 6h stale, an age stamp past 10 minutes.
"""

import json
import time
import unittest
from datetime import datetime, timedelta, timezone

from api.hub import ContextCardBody, get_context_card, set_context_card
from db.database import get_db, release_db
from db.schema import init_db
from services.identity_context import build_context_card_context


async def _reset_context_card():
    db = await get_db()
    try:
        await init_db(db)
        await db.execute("DELETE FROM settings WHERE key = ?", ("hub_context_card",))
        await db.commit()
    finally:
        await release_db(db)


async def _write_card(card: dict):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            ("hub_context_card", json.dumps(card), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    finally:
        await release_db(db)


class ContextCardApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _reset_context_card()

    async def test_empty_card_returns_empty_dict(self):
        result = await get_context_card()
        self.assertEqual(result["card"], {})

    async def test_save_and_read_roundtrip(self):
        result = await set_context_card(ContextCardBody(outfit="cozy sweater", room="kitchen"))
        self.assertEqual(result["card"]["outfit"], "cozy sweater")
        self.assertEqual(result["card"]["room"], "kitchen")
        self.assertIn("updated_at", result["card"])

        result = await get_context_card()
        self.assertEqual(result["card"]["outfit"], "cozy sweater")

    async def test_patch_style_save_preserves_untouched_fields(self):
        await set_context_card(ContextCardBody(outfit="pajamas", hair="braided"))
        result = await set_context_card(ContextCardBody(energy="low"))
        self.assertEqual(result["card"]["outfit"], "pajamas")
        self.assertEqual(result["card"]["hair"], "braided")
        self.assertEqual(result["card"]["energy"], "low")

    async def test_fields_are_clamped(self):
        result = await set_context_card(ContextCardBody(freeform="x" * 1000))
        self.assertEqual(len(result["card"]["freeform"]), 400)

    async def test_every_save_refreshes_updated_at(self):
        first = await set_context_card(ContextCardBody(outfit="a"))
        time.sleep(0.01)
        second = await set_context_card(ContextCardBody(outfit="b"))
        self.assertGreaterEqual(second["card"]["updated_at"], first["card"]["updated_at"])


class ContextCardOrientationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _reset_context_card()

    async def test_no_card_means_silence(self):
        self.assertEqual(build_context_card_context(), "")

    async def test_fresh_card_surfaces_filled_fields_only(self):
        await _write_card({
            "outfit": "cozy sweater",
            "hair": "",
            "room": "kitchen",
            "updated_at": int(datetime.now(timezone.utc).timestamp()),
        })
        text = build_context_card_context()
        self.assertIn("wearing cozy sweater", text)
        self.assertIn("in the kitchen", text)
        self.assertNotIn("hair", text.lower())

    async def test_fresh_card_has_no_age_stamp_under_10_minutes(self):
        await _write_card({
            "outfit": "cozy sweater",
            "updated_at": int(datetime.now(timezone.utc).timestamp()),
        })
        text = build_context_card_context()
        self.assertNotIn("ago", text)

    async def test_older_than_10_minutes_gets_age_stamp(self):
        ts = int((datetime.now(timezone.utc) - timedelta(minutes=45)).timestamp())
        await _write_card({"outfit": "cozy sweater", "updated_at": ts})
        text = build_context_card_context()
        self.assertIn("ago", text)

    async def test_stale_past_6_hours_goes_silent(self):
        ts = int((datetime.now(timezone.utc) - timedelta(hours=7)).timestamp())
        await _write_card({"outfit": "cozy sweater", "updated_at": ts})
        self.assertEqual(build_context_card_context(), "")

    async def test_all_blank_fields_means_silence(self):
        await _write_card({"outfit": "", "hair": "", "updated_at": int(datetime.now(timezone.utc).timestamp())})
        self.assertEqual(build_context_card_context(), "")


if __name__ == "__main__":
    unittest.main()
