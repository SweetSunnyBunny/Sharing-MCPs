"""Persistent Canvas/artifact system (#32) -- CRUD API and sharing rules."""

import unittest

from db.database import get_db, release_db
from db.schema import init_db
from services.canvas_store import persist_canvas_blocks

from api import canvases


class _CanvasApiTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db = await get_db()
        try:
            await init_db(db)
            # Full isolation: tests assert on identity-scoped TOTAL counts,
            # and this file's tests share one file-based DB for the whole
            # pytest session (conftest.py) -- without a wipe, an earlier
            # test's canvases for the same identity leak into a later
            # test's count assertions.
            await db.execute("DELETE FROM canvas_shares")
            await db.execute("DELETE FROM canvases")
            await db.commit()
        finally:
            await release_db(db)

    async def _make_canvas(self, identity="Avery", title="Test", content="hello", conv="conv-1"):
        db = await get_db()
        try:
            ids = await persist_canvas_blocks(
                db, identity=identity, conversation_id=conv,
                content=f'<canvas title="{title}">{content}</canvas>',
                source_message_id="msg-x",
            )
        finally:
            await release_db(db)
        return ids[0]


class ListCanvasesTests(_CanvasApiTestBase):
    async def test_unknown_identity_is_rejected(self):
        result = await canvases.list_canvases(identity="NotARealBoy")
        self.assertEqual(result.status_code, 400)

    async def test_owner_sees_own_canvas(self):
        await self._make_canvas(identity="Avery", title="Mine")
        result = await canvases.list_canvases(identity="Avery")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "Mine")
        self.assertIsNone(result["items"][0]["shared_by"])

    async def test_non_owner_does_not_see_unshared_canvas(self):
        await self._make_canvas(identity="Avery", title="Private")
        result = await canvases.list_canvases(identity="Claude")
        self.assertEqual(result["total"], 0)

    async def test_pagination_returns_honest_total_and_page(self):
        for i in range(5):
            await self._make_canvas(identity="Avery", title=f"Item {i}")
        page1 = await canvases.list_canvases(identity="Avery", limit=2, offset=0)
        self.assertEqual(page1["total"], 5)
        self.assertEqual(len(page1["items"]), 2)
        page2 = await canvases.list_canvases(identity="Avery", limit=2, offset=2)
        self.assertEqual(len(page2["items"]), 2)
        self.assertNotEqual(
            [i["id"] for i in page1["items"]], [i["id"] for i in page2["items"]],
        )

    async def test_limit_is_clamped_to_max(self):
        result = await canvases.list_canvases(identity="Avery", limit=99999)
        self.assertLessEqual(result["limit"], canvases._MAX_LIMIT)

    async def test_pinned_canvases_sort_first(self):
        first_id = await self._make_canvas(identity="Avery", title="Old")
        await self._make_canvas(identity="Avery", title="New")
        db = await get_db()
        try:
            await db.execute("UPDATE canvases SET pinned = 1 WHERE id = ?", (first_id,))
            await db.commit()
        finally:
            await release_db(db)
        result = await canvases.list_canvases(identity="Avery")
        self.assertEqual(result["items"][0]["title"], "Old")


class GetCanvasTests(_CanvasApiTestBase):
    async def test_owner_can_fetch_full_content(self):
        cid = await self._make_canvas(identity="Avery", content="the full body")
        result = await canvases.get_canvas(cid, identity="Avery")
        self.assertEqual(result["content"], "the full body")

    async def test_stranger_gets_403(self):
        cid = await self._make_canvas(identity="Avery")
        result = await canvases.get_canvas(cid, identity="Claude")
        self.assertEqual(result.status_code, 403)

    async def test_missing_canvas_gets_404(self):
        result = await canvases.get_canvas(999999, identity="Avery")
        self.assertEqual(result.status_code, 404)

    async def test_shared_with_identity_can_fetch(self):
        cid = await self._make_canvas(identity="Avery")

        class _FakeShareRequest:
            async def json(self):
                return {"identity": "Avery", "share_with": "Claude"}

        share_result = await canvases.share_canvas(cid, _FakeShareRequest())
        self.assertEqual(share_result["ok"], True)

        result = await canvases.get_canvas(cid, identity="Claude")
        self.assertEqual(result["title"], "Test")
        self.assertEqual(result["shared_by"], "Avery")


class DeleteCanvasTests(_CanvasApiTestBase):
    async def test_owner_can_delete(self):
        cid = await self._make_canvas(identity="Avery")
        result = await canvases.delete_canvas(cid, identity="Avery")
        self.assertEqual(result["ok"], True)
        followup = await canvases.get_canvas(cid, identity="Avery")
        self.assertEqual(followup.status_code, 404)

    async def test_non_owner_cannot_delete(self):
        cid = await self._make_canvas(identity="Avery")
        result = await canvases.delete_canvas(cid, identity="Claude")
        self.assertEqual(result.status_code, 403)
        # Still there afterward.
        followup = await canvases.get_canvas(cid, identity="Avery")
        self.assertEqual(followup["title"], "Test")

    async def test_delete_cascades_shares(self):
        class _FakeShareRequest:
            async def json(self):
                return {"identity": "Avery", "share_with": "Claude"}

        cid = await self._make_canvas(identity="Avery")
        await canvases.share_canvas(cid, _FakeShareRequest())
        await canvases.delete_canvas(cid, identity="Avery")

        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT * FROM canvas_shares WHERE canvas_id = ?", (cid,),
            )
        finally:
            await release_db(db)
        self.assertEqual(len(rows), 0)


class ShareCanvasTests(_CanvasApiTestBase):
    class _FakeRequest:
        def __init__(self, payload):
            self._payload = payload

        async def json(self):
            return self._payload

    async def test_owner_can_share(self):
        cid = await self._make_canvas(identity="Avery")
        result = await canvases.share_canvas(cid, self._FakeRequest({"identity": "Avery", "share_with": "Claude"}))
        self.assertEqual(result["ok"], True)
        listing = await canvases.list_canvases(identity="Claude")
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["shared_by"], "Avery")

    async def test_non_owner_cannot_share(self):
        cid = await self._make_canvas(identity="Avery")
        result = await canvases.share_canvas(cid, self._FakeRequest({"identity": "Claude", "share_with": "Sage"}))
        self.assertEqual(result.status_code, 403)

    async def test_cannot_share_with_unknown_identity(self):
        cid = await self._make_canvas(identity="Avery")
        result = await canvases.share_canvas(cid, self._FakeRequest({"identity": "Avery", "share_with": "NotReal"}))
        self.assertEqual(result.status_code, 400)

    async def test_cannot_share_with_self(self):
        cid = await self._make_canvas(identity="Avery")
        result = await canvases.share_canvas(cid, self._FakeRequest({"identity": "Avery", "share_with": "Avery"}))
        self.assertEqual(result.status_code, 400)

    async def test_sharing_twice_is_idempotent(self):
        cid = await self._make_canvas(identity="Avery")
        req = lambda: self._FakeRequest({"identity": "Avery", "share_with": "Claude"})
        await canvases.share_canvas(cid, req())
        result = await canvases.share_canvas(cid, req())
        self.assertEqual(result["ok"], True)
        listing = await canvases.list_canvases(identity="Claude")
        self.assertEqual(listing["total"], 1)  # not duplicated

    async def test_unshare_revokes_access(self):
        cid = await self._make_canvas(identity="Avery")
        await canvases.share_canvas(cid, self._FakeRequest({"identity": "Avery", "share_with": "Claude"}))
        result = await canvases.unshare_canvas(cid, "Claude", identity="Avery")
        self.assertEqual(result["ok"], True)
        followup = await canvases.get_canvas(cid, identity="Claude")
        self.assertEqual(followup.status_code, 403)

    async def test_non_owner_cannot_unshare(self):
        cid = await self._make_canvas(identity="Avery")
        await canvases.share_canvas(cid, self._FakeRequest({"identity": "Avery", "share_with": "Claude"}))
        result = await canvases.unshare_canvas(cid, "Claude", identity="Sage")
        self.assertEqual(result.status_code, 403)


if __name__ == "__main__":
    unittest.main()
