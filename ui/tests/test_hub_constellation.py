"""Memory constellation — honesty fields (total_embedded, built_at), the
?limit= 'more sky' cache bypass, and per-star nearest-neighbor index arrays
for the teaser constellation lines."""

import unittest
import uuid
from datetime import datetime, timezone

import numpy as np

import api.hub as hub
from api.hub import _build_constellation_sync, get_constellation
from db.database import get_db, release_db
from db.schema import init_db
from services.embedding_service import _EMBEDDING_DIM, _pack_vector


def _vec(seed: int) -> np.ndarray:
    """Deterministic unit-ish 384-dim vector."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal(_EMBEDDING_DIM).astype(np.float32)


def _reset_constellation_cache():
    hub._constellation_cache = None
    hub._constellation_cache_at = 0.0
    hub._constellation_cache_limit = 0


async def _seed_embedded_messages(count: int):
    """Seed a conversation with `count` embedded messages (long enough content,
    not autowake) so the constellation endpoint has stars to chart."""
    now = datetime.now(timezone.utc).isoformat()
    conv_id = f"conv-const-{uuid.uuid4().hex[:8]}"
    db = await get_db()
    try:
        await init_db(db)
        await db.execute("DELETE FROM message_embeddings")
        await db.execute("DELETE FROM messages")
        await db.execute(
            "INSERT INTO conversations (id, identity, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conv_id, "claude", now, now),
        )
        for i in range(count):
            msg_id = f"msg-const-{i:04d}"
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, identity, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (msg_id, conv_id, "assistant" if i % 2 else "user", "claude",
                 f"star number {i:04d} — a memory long enough to make the cut", now),
            )
            await db.execute(
                "INSERT INTO message_embeddings (message_id, embedding) VALUES (?, ?)",
                (msg_id, _pack_vector(_vec(i))),
            )
        await db.commit()
    finally:
        await release_db(db)


class BuildConstellationNeighborTests(unittest.TestCase):
    def _rows(self, vecs):
        return [
            (_pack_vector(v), f"content long enough for star {i} to shine", "claude",
             "assistant", "2026-07-08T00:00:00", "claude")
            for i, v in enumerate(vecs)
        ]

    def test_neighbors_are_valid_index_arrays(self):
        result = _build_constellation_sync(self._rows([_vec(i) for i in range(12)]))
        self.assertEqual(result["count"], 12)
        for i, star in enumerate(result["stars"]):
            self.assertIn("nn", star)
            self.assertEqual(len(star["nn"]), 6)
            self.assertNotIn(i, star["nn"], "a star must not be its own neighbor")
            for j in star["nn"]:
                self.assertTrue(0 <= j < 12)

    def test_nearest_neighbor_reflects_cosine_similarity(self):
        # Two tight clusters — each star's first neighbor stays in its cluster.
        base_a, base_b = _vec(1000), _vec(2000)
        vecs = [base_a + 0.01 * _vec(i) for i in range(3)] \
             + [base_b + 0.01 * _vec(10 + i) for i in range(3)]
        result = _build_constellation_sync(self._rows(vecs))
        for i, star in enumerate(result["stars"]):
            cluster = range(0, 3) if i < 3 else range(3, 6)
            self.assertIn(star["nn"][0], cluster,
                          f"star {i}'s nearest neighbor left its cluster")

    def test_fewer_stars_than_neighbor_count(self):
        result = _build_constellation_sync(self._rows([_vec(1), _vec(2)]))
        self.assertEqual(result["stars"][0]["nn"], [1])
        self.assertEqual(result["stars"][1]["nn"], [0])

    def test_single_star_has_no_neighbors(self):
        result = _build_constellation_sync(self._rows([_vec(7)]))
        self.assertEqual(result["stars"][0]["nn"], [])


class ConstellationEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _reset_constellation_cache()

    async def test_payload_gains_honesty_fields(self):
        await _seed_embedded_messages(10)
        result = await get_constellation()
        self.assertEqual(result["count"], 10)
        self.assertEqual(result["total_embedded"], 10)
        self.assertIsInstance(result["built_at"], int)
        self.assertGreater(result["built_at"], 0)
        for star in result["stars"]:
            self.assertIn("nn", star)

    async def test_cache_serves_repeat_default_requests(self):
        await _seed_embedded_messages(10)
        first = await get_constellation()
        second = await get_constellation()
        self.assertIs(first, second)

    async def test_limit_rebuilds_past_the_cache(self):
        await _seed_embedded_messages(60)
        small = await get_constellation(limit=45)
        self.assertEqual(small["count"], 45)
        # More sky asked for → the fresh cache is bypassed and rebuilt bigger.
        bigger = await get_constellation(limit=51)
        self.assertEqual(bigger["count"], 51)
        self.assertEqual(bigger["total_embedded"], 60)
        # A smaller-or-equal ask rides the bigger cache instead of rebuilding.
        cached = await get_constellation(limit=45)
        self.assertIs(cached, bigger)
        default = await get_constellation()
        self.assertIs(default, bigger)

    async def test_empty_sky_is_honest_not_500(self):
        await _seed_embedded_messages(0)
        result = await get_constellation()
        self.assertEqual(result["stars"], [])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["total_embedded"], 0)
        self.assertIn("built_at", result)


if __name__ == "__main__":
    unittest.main()
