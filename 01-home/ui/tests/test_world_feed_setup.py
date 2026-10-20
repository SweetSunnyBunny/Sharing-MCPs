"""Fresh fictional worlds and edits use only an in-memory database."""

import unittest

import aiosqlite

from db.schema import SCHEMA
from services import world_feed as feed


class WorldFeedSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_empty_default_is_generic_and_not_automatic(self):
        world = await feed.ensure_default_world(self.db)
        self.assertEqual(world["story_identity"], "Avery")
        self.assertEqual(world["story_branch"], "")
        self.assertFalse(world["posting_enabled"])
        self.assertEqual(await feed.list_profiles(self.db, world["id"]), [])
        self.assertEqual(await feed.list_feed(self.db, world["id"]), [])

    async def test_existing_default_world_is_not_reconfigured(self):
        world = await feed.ensure_default_world(self.db)
        await feed.update_world(self.db, world["id"], {
            "name": "My Existing Story", "story_identity": "Rowan",
            "story_branch": "chapter-one", "posting_enabled": True,
            "metadata": {"private_custom_setting": "test value"},
        })
        preserved = await feed.ensure_default_world(self.db)
        self.assertEqual(preserved["name"], "My Existing Story")
        self.assertEqual(preserved["story_identity"], "Rowan")
        self.assertEqual(preserved["story_branch"], "chapter-one")
        self.assertTrue(preserved["posting_enabled"])
        self.assertEqual(preserved["metadata"], {"private_custom_setting": "test value"})

    async def test_create_blank_branch_and_merge_photo_style_with_activity(self):
        world = await feed.create_world(self.db, {
            "slug": "cafe-friends", "name": "Cafe Friends",
            "metadata": {"photos": {"enabled": False, "profile_ids": []}, "custom": 7},
        })
        self.assertEqual(world["story_identity"], "Avery")
        self.assertEqual(world["story_branch"], "")
        saved = await feed.update_world_settings(self.db, world["id"], {
            "story_identity": "Sage", "story_branch": "",
            "activity_daily_limit": 3, "activity_auto_publish": False,
            "activity_scene_reactions": False,
            "metadata": {"photo_style": "Watercolour"},
        })
        self.assertEqual(saved["metadata"]["photo_style"], "Watercolour")
        self.assertEqual(saved["metadata"]["photos"], {"enabled": False, "profile_ids": []})
        self.assertEqual(saved["metadata"]["custom"], 7)
        self.assertEqual(saved["metadata"]["activity"]["daily_limit"], 3)
        self.assertEqual(saved["story_identity"], "Sage")
        self.assertEqual(saved["story_branch"], "")
        self.assertFalse(saved["posting_enabled"])


if __name__ == "__main__":
    unittest.main()
