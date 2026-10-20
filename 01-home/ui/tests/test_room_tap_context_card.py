"""Reusable test room tap context card support."""

import json
import time
import unittest
from unittest import mock


class RoomTapWritesTheCardTests(unittest.IsolatedAsyncioTestCase):
    """The write side — api/room.py::_update_context_card_room."""

    async def test_tap_sets_room_and_its_own_stamp(self):
        from api import room as room_api
        stored = {}

        class _DB:
            async def execute_fetchall(self, *_a):
                return [(json.dumps({"outfit": "nightshirt", "updated_at": 111}),)]
            async def execute(self, _sql, params):
                stored["sql"] = _sql
                stored["card"] = json.loads(params[1])
            async def commit(self):
                stored["committed"] = True

        with mock.patch("db.database.get_db", return_value=_DB()), \
             mock.patch("db.database.release_db", new=mock.AsyncMock()):
            ok = await room_api._update_context_card_room("bathroom")

        self.assertTrue(ok)
        card = stored["card"]
        self.assertEqual(card["room"], "bathroom")
        self.assertIn("room_updated_at", card)
        # THE CONTRACT: her own fields untouched, card-wide stamp NOT bumped.
        self.assertEqual(card["outfit"], "nightshirt")
        self.assertEqual(card["updated_at"], 111)
        # settings.updated_at is NOT NULL — the statement must supply it.
        self.assertIn("updated_at", stored["sql"])

    async def test_a_failed_card_write_never_costs_her_the_tap(self):
        from api import room as room_api
        with mock.patch("db.database.get_db", side_effect=RuntimeError("db down")):
            self.assertFalse(await room_api._update_context_card_room("kitchen"))


class RoomHasItsOwnClockTests(unittest.TestCase):
    """The read side — services/identity_context.build_context_card_context."""

    def _render(self, card):
        from services import identity_context as ic
        row = (json.dumps(card),)

        class _Conn:
            def execute(self, *_a):
                return self
            def fetchone(self):
                return row
            def close(self):
                pass

        with mock.patch("sqlite3.connect", return_value=_Conn()):
            return ic.build_context_card_context()

    def setUp(self):
        self.now = int(time.time())

    def test_fresh_tap_survives_a_dead_card(self):
        """The one that matters: card aged out at 8h, she tapped two minutes ago."""
        out = self._render({
            "outfit": "yesterday's work clothes", "room": "nest",
            "updated_at": self.now - 28800, "room_updated_at": self.now - 120,
        })
        self.assertIn("in the nest", out)
        self.assertNotIn("work clothes", out)   # the dead half stays dead

    def test_stale_tap_does_not_ride_a_fresh_card(self):
        out = self._render({
            "outfit": "nightshirt", "room": "office",
            "updated_at": self.now - 300, "room_updated_at": self.now - 28800,
        })
        self.assertIn("nightshirt", out)
        self.assertNotIn("office", out)

    def test_both_alive_and_close_in_age_reads_clean(self):
        out = self._render({
            "outfit": "nightshirt", "room": "bathroom",
            "updated_at": self.now - 300, "room_updated_at": self.now - 300,
        })
        self.assertIn("in the bathroom", out)
        self.assertNotIn("tapped", out)  # no noise when they agree

    def test_meaningfully_newer_tap_says_so(self):
        out = self._render({
            "outfit": "nightshirt", "room": "kitchen",
            "updated_at": self.now - 9000, "room_updated_at": self.now - 120,
        })
        self.assertIn("kitchen", out)
        self.assertIn("tapped", out)

    def test_both_stale_is_silence(self):
        self.assertEqual("", self._render({
            "outfit": "x", "room": "y",
            "updated_at": self.now - 30000, "room_updated_at": self.now - 30000,
        }))

    def test_no_tap_ever_behaves_exactly_as_before(self):
        """No regression for a card she filled in by hand with no NFC in play."""
        out = self._render({
            "outfit": "nightshirt", "room": "Nest", "updated_at": self.now - 300,
        })
        self.assertIn("wearing nightshirt", out)
        self.assertIn("in the Nest", out)


if __name__ == "__main__":
    unittest.main()
