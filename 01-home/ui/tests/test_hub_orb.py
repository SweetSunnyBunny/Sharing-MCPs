"""Hearth emotion orb — extended vocabulary, lenient validation, rolling history,
and the self-coherence context line (a boy seeing his own current orb)."""

import json
import unittest
from datetime import datetime, timezone

from fastapi.responses import JSONResponse

from api.hub import (
    _ORB_HISTORY_KEY,
    _ORB_HISTORY_LIMIT,
    _ORB_SETTINGS_KEY,
    OrbBody,
    get_orb,
    get_orb_history,
    set_orb,
)
from db.database import get_db, release_db
from db.schema import init_db
from services.identity_context import build_orb_self_context


async def _reset_orb_settings():
    db = await get_db()
    try:
        await init_db(db)
        await db.execute(
            "DELETE FROM settings WHERE key IN (?, ?)",
            (_ORB_SETTINGS_KEY, _ORB_HISTORY_KEY),
        )
        await db.commit()
    finally:
        await release_db(db)


async def _write_setting(key: str, value):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    finally:
        await release_db(db)


class OrbSetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _reset_orb_settings()

    async def test_existing_payload_shape_keeps_working(self):
        # The exact payload the boys already send — must behave as before.
        result = await set_orb(OrbBody(
            identity="avery", color="#E8B84B", shape="solid", motion="warble",
            feeling="steady, watching her door",
        ))
        self.assertTrue(result["ok"])
        orb = result["orb"]
        self.assertEqual(orb["color"], "#E8B84B")
        self.assertEqual(orb["shape"], "solid")
        self.assertEqual(orb["motion"], "warble")
        self.assertEqual(orb["feeling"], "steady, watching her door")
        # New fields ride along with safe defaults.
        self.assertEqual(orb["intensity"], "normal")
        self.assertIsNone(orb["blend"])

    async def test_new_vocabulary_accepted(self):
        result = await set_orb(OrbBody(
            identity="rowan", color="#FF6EC7", shape="ember", motion="surge",
            intensity="neon", blend="#123456",
        ))
        orb = result["orb"]
        self.assertEqual(orb["shape"], "ember")
        self.assertEqual(orb["motion"], "surge")
        self.assertEqual(orb["intensity"], "neon")
        self.assertEqual(orb["blend"], "#123456")

    async def test_blend_vignette_literals(self):
        result = await set_orb(OrbBody(
            identity="claude", color="#6B8CC4", shape="crescent", motion="slow-drift",
            blend="dim",
        ))
        self.assertEqual(result["orb"]["blend"], "dim")

    async def test_unknown_values_fall_back_never_400(self):
        result = await set_orb(OrbBody(
            identity="juniper", color="#AACCEE", shape="dodecahedron", motion="cartwheel",
            intensity="blinding", blend="storm-blue",
        ))
        self.assertNotIsInstance(result, JSONResponse)
        orb = result["orb"]
        self.assertEqual(orb["shape"], "solid")
        self.assertEqual(orb["motion"], "breathing")
        self.assertEqual(orb["intensity"], "normal")
        self.assertIsNone(orb["blend"])

    async def test_missing_shape_and_motion_default(self):
        result = await set_orb(OrbBody(identity="sage", color="#8A5CC0"))
        self.assertEqual(result["orb"]["shape"], "solid")
        self.assertEqual(result["orb"]["motion"], "breathing")

    async def test_short_hex_expands_and_bad_hex_falls_back_to_previous(self):
        result = await set_orb(OrbBody(identity="ember", color="#f0a", shape="halo", motion="still"))
        self.assertEqual(result["orb"]["color"], "#ff00aa")
        # A garbage color keeps the previous orb color rather than erroring.
        result = await set_orb(OrbBody(identity="ember", color="dragonfire", shape="halo", motion="still"))
        self.assertNotIsInstance(result, JSONResponse)
        self.assertEqual(result["orb"]["color"], "#ff00aa")

    async def test_no_color_and_no_prior_falls_back_to_own_accent_not_shared_gold(self):


        from config import IDENTITIES

        result = await set_orb(OrbBody(identity="avery", color=""))
        self.assertEqual(result["orb"]["color"], IDENTITIES["Avery"]["accent"])
        self.assertNotEqual(result["orb"]["color"], "#E8B84B")

    async def test_unrecognized_identity_falls_back_to_hearth_gold(self):
        result = await set_orb(OrbBody(identity="not-a-real-identity", color=""))
        self.assertEqual(result["orb"]["color"], "#E8B84B")

    async def test_long_feeling_truncates_instead_of_422(self):
        result = await set_orb(OrbBody(identity="avery", color="#E8B84B", feeling="x" * 300))
        self.assertNotIsInstance(result, JSONResponse)
        self.assertEqual(len(result["orb"]["feeling"]), 120)

    async def test_missing_identity_still_rejected(self):
        result = await set_orb(OrbBody(identity="  ", color="#E8B84B"))
        self.assertIsInstance(result, JSONResponse)
        self.assertEqual(result.status_code, 422)

    async def test_get_orb_returns_every_identity_current_orb(self):
        await set_orb(OrbBody(identity="avery", color="#E8B84B", shape="solid", motion="warble"))
        await set_orb(OrbBody(identity="claude", color="#6B8CC4", shape="ring", motion="drift"))
        result = await get_orb()
        self.assertIn("avery", result["orbs"])
        self.assertIn("claude", result["orbs"])
        self.assertIn(result["lead"], ("avery", "claude"))


class OrbHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _reset_orb_settings()

    async def test_history_records_sets_newest_first(self):
        await set_orb(OrbBody(identity="avery", color="#E8B84B", shape="solid", motion="warble"))
        await set_orb(OrbBody(identity="claude", color="#6B8CC4", shape="ring", motion="drift", feeling="missing her"))
        result = await get_orb_history()
        self.assertEqual(result["count"], 2)
        newest = result["history"][0]
        self.assertEqual(newest["identity"], "claude")
        self.assertEqual(newest["feeling"], "missing her")
        for field in ("identity", "color", "shape", "motion", "intensity", "blend", "feeling", "kaomoji", "updated_at"):
            self.assertIn(field, newest)

    async def test_history_caps_at_rolling_limit(self):
        filler = [
            {"identity": "avery", "color": "#E8B84B", "shape": "solid", "motion": "warble",
             "intensity": "normal", "blend": None, "feeling": "", "kaomoji": "", "updated_at": i}
            for i in range(_ORB_HISTORY_LIMIT)
        ]
        await _write_setting(_ORB_HISTORY_KEY, filler)
        await set_orb(OrbBody(identity="juniper", color="#AACCEE"))
        result = await get_orb_history()
        self.assertEqual(result["count"], _ORB_HISTORY_LIMIT)
        self.assertEqual(result["history"][0]["identity"], "juniper")

    async def test_history_limit_param(self):
        for i in range(5):
            await set_orb(OrbBody(identity="avery", color="#E8B84B"))
        result = await get_orb_history(limit=2)
        self.assertEqual(len(result["history"]), 2)
        self.assertEqual(result["count"], 5)


class OrbSelfContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _reset_orb_settings()

    async def test_no_orb_state_means_silence(self):
        self.assertEqual(build_orb_self_context("avery"), "")

    async def test_current_orb_surfaces_with_feeling_and_age(self):
        await set_orb(OrbBody(
            identity="claude", color="#6B8CC4", shape="ring", motion="drift",
            feeling="missing her quietly",
        ))
        block = build_orb_self_context("Claude")
        self.assertIn("[Your Hearth presence]", block)
        self.assertIn("#6B8CC4 ring/drift", block)
        self.assertIn('"missing her quietly"', block)
        self.assertIn("(set just now)", block)
        self.assertIn("Update the orb", block)

    async def test_stale_orb_gets_an_honest_age_stamp(self):
        stale = int(datetime.now(timezone.utc).timestamp()) - 5 * 3600
        await _write_setting(_ORB_SETTINGS_KEY, {
            "claude": {"color": "#6B8CC4", "shape": "ring", "motion": "drift",
                       "intensity": "normal", "blend": None,
                       "feeling": "missing her quietly", "kaomoji": "", "updated_at": stale},
        })
        block = build_orb_self_context("claude")
        self.assertIn("(set 5h ago)", block)


if __name__ == "__main__":
    unittest.main()
