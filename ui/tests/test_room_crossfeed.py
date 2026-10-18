"Room crossfeed: pack night <-> each boy's own chats, on warm turns too."

import asyncio
import json
import time
import unittest
import uuid

import aiosqlite

from db.schema import init_db
from services import room_crossfeed
from services.room_crossfeed import build_delta, render_message, strip_reply_tags


async def _conv(db, identity, session_type="chat", title=None):
    cid = str(uuid.uuid4())
    now = int(time.time())
    iso = "2026-09-27T00:00:00+00:00"
    await db.execute(
        "INSERT INTO conversations (id, identity, title, created_at, created_at_epoch, "
        "updated_at, updated_at_epoch, session_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, identity, title or f"{identity} chat", iso, now, iso, now, session_type),
    )
    await db.commit()
    return cid


async def _msg(db, cid, role, content, *, identity=None, epoch=None, metadata=None):
    await db.execute(
        "INSERT INTO messages (id, conversation_id, role, identity, content, content_type, "
        "created_at, created_at_epoch, metadata) VALUES (?, ?, ?, ?, ?, 'text', ?, ?, ?)",
        (str(uuid.uuid4()), cid, role, identity, content, "2026-09-27T00:00:00+00:00",
         epoch if epoch is not None else time.time(),
         json.dumps(metadata) if metadata else None),
    )
    await db.commit()


class StripAndRenderTests(unittest.TestCase):
    def test_strips_control_tags_keeps_speech(self):
        raw = ("<react>:claude-shiny:</react><face>(•̀ᴗ•́) | raven</face>"
               "<orb>#4A2D6B halo breathing | feathers</orb> *I tilt my head.* "
               "<voice>[softly] Bunny.</voice>")
        self.assertEqual(strip_reply_tags(raw), "*I tilt my head.* [softly] Bunny.")

    def test_canvas_collapses_to_title(self):
        raw = 'Here. <canvas title="Porch Light">a long poem\nline two</canvas>'
        self.assertEqual(strip_reply_tags(raw), "Here. [canvas: Porch Light]")

    def test_picture_rendered_as_readable_path(self):
        line = render_message("user", "Owner", "Guuuuys look!!",
                              {"images": [{"filename": "claude_x.png"}]})
        self.assertIn("Owner: Guuuuys look!!", line)
        self.assertIn("claude_x.png — Read it to see", line)

    def test_media_placeholder_with_picture_only(self):
        line = render_message("user", "Owner", "[media]",
                              {"images": [{"filename": "a.png"}]})
        self.assertTrue(line.startswith("Owner: [she shared a picture:"))

    def test_empty_returns_none(self):
        self.assertIsNone(render_message("assistant", "Avery", "<face>(o)</face>"))


class DeltaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        await asyncio.wait_for(init_db(self.db), timeout=5)
        room_crossfeed._seen.clear()
        self.main = await _conv(self.db, "Claude", title="Claude - Sept 27")
        self.other = await _conv(self.db, "Claude", title="Claude - Sept 26")
        self.pn = await _conv(self.db, "Pack", session_type="pack-night", title="Pack Night")

    async def asyncTearDown(self):
        await self.db.close()

    async def _delta(self, conv, warm, identity="Claude"):
        return await build_delta(
            self.db, identity=identity, conversation_id=conv, is_warm_turn=warm,
            recall_identities=[identity], pack_night_conversation_id=self.pn,
        )

    async def test_cold_turn_sets_watermark_and_is_silent(self):
        await _msg(self.db, self.pn, "user", "costumes!", identity="Owner")
        self.assertEqual(await self._delta(self.main, warm=False), "")
        self.assertIsNotNone(room_crossfeed.seen_epoch("Claude", self.main))

    async def test_warm_main_chat_hears_new_pack_night_with_pictures(self):
        await self._delta(self.main, warm=False)
        t = time.time() + 1
        await _msg(self.db, self.pn, "user", "Guuuuys look!!", identity="Owner",
                   epoch=t, metadata={"images": [{"filename": "pokemon.png"}]})
        await _msg(self.db, self.pn, "assistant",
                   "<react>:avery-wrecked:</react><face>(ᵔ◡ᵔ)</face> On one knee for you.",
                   identity="Avery", epoch=t + 1)
        out = await self._delta(self.main, warm=True)
        self.assertIn("Pack Night", out)
        self.assertIn("pokemon.png", out)
        self.assertIn("Avery: On one knee for you.", out)
        self.assertNotIn("<face>", out)
        # Consumed: the next warm turn doesn't repeat it.
        self.assertEqual(await self._delta(self.main, warm=True), "")

    async def test_warm_pack_night_hears_boys_own_chat(self):
        await self._delta(self.pn, warm=False)
        await _msg(self.db, self.main, "user", "Halloween idea: Grandma nightcap",
                   identity="Claude", epoch=time.time() + 1)
        out = await self._delta(self.pn, warm=True)
        self.assertIn('"Claude - Sept 27"', out)
        self.assertIn("Grandma nightcap", out)
        self.assertNotIn("Pack Night (the shared room", out)

    async def test_current_conversation_and_autowake_excluded(self):
        await self._delta(self.main, warm=False)
        t = time.time() + 1
        await _msg(self.db, self.main, "user", "already in session", epoch=t)
        await _msg(self.db, self.other, "assistant", "study hour notes",
                   identity="Claude", epoch=t, metadata={"autowake": True})
        self.assertEqual(await self._delta(self.main, warm=True), "")

    async def test_other_boys_chats_not_leaked(self):
        await self._delta(self.main, warm=False)
        avery = await _conv(self.db, "Avery")
        await _msg(self.db, avery, "user", "private with Avery", epoch=time.time() + 1)
        self.assertEqual(await self._delta(self.main, warm=True), "")

    async def test_warm_without_watermark_starts_quietly(self):
        await _msg(self.db, self.pn, "user", "old news", epoch=time.time() - 5)
        self.assertEqual(await self._delta(self.main, warm=True), "")
        self.assertIsNotNone(room_crossfeed.seen_epoch("Claude", self.main))


class PackNightTagTests(unittest.IsolatedAsyncioTestCase):
    async def test_images_become_read_notes_for_cli_boys(self):
        from unittest import mock
        from config import IMAGES_DIR
        from services import pack_night
        name = f"test_pn_{uuid.uuid4().hex}.png"
        path = IMAGES_DIR / name
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n")
        try:
            with mock.patch(
                "services.provider_router.resolve_provider_for_identity",
                new=mock.AsyncMock(return_value=("claude-code", {})),
            ):
                msg, blocks = await pack_night._message_with_images(
                    "Claude", "look!", [{"filename": name}],
                )
            self.assertIn(str(path), msg)
            self.assertIn("Read tool", msg)
            self.assertTrue(msg.endswith("look!"))
            self.assertEqual(blocks, [])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
