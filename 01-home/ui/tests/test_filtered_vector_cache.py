"""Filtered vector-cache search (#29).

The default identity-scoped semantic_search() path — what every boy's own
recall actually calls — used to run a live SQL JOIN + per-call BLOB unpack
on every search. This wires it onto the SAME cached corpus the unfiltered
path already used, filtered with numpy boolean masks over parallel
identity/role/date arrays. Tests avoid loading the real sentence-transformers
model: query vectors and embeddings are synthetic, matching the existing
constellation test's pattern (services.embedding_service._pack_vector +
deterministic seeded vectors).
"""

import unittest
import uuid
from datetime import datetime, timezone

import numpy as np

from db.database import get_db, release_db
from db.schema import init_db
from services import embedding_service as es
from services.embedding_service import (
    _EMBEDDING_DIM,
    _build_corpus_dict,
    _pack_vector,
    _search_filtered_cached,
)


def _vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


async def _wipe_embedding_test_state():
    """Leave the shared temp test DB clean — message_embeddings FKs onto
    messages, so it must be cleared FIRST or a later test file's own
    `DELETE FROM messages` (e.g. test_hearth_author's _reset_hearth_state)
    fails with a FOREIGN KEY constraint against rows we left behind."""
    db = await get_db()
    try:
        await init_db(db)
        await db.execute("DELETE FROM message_embeddings")
        await db.execute("DELETE FROM messages")
        await db.execute("DELETE FROM conversations")
        await db.commit()
    finally:
        await release_db(db)


async def _seed(rows):
    """rows: (msg_id, conv_id, conv_identity, role, msg_identity, content, created_at, vec)"""
    db = await get_db()
    try:
        await init_db(db)
        await db.execute("DELETE FROM message_embeddings")
        await db.execute("DELETE FROM messages")
        seen_convs = set()
        for msg_id, conv_id, conv_identity, role, msg_identity, content, created_at, vec in rows:
            if conv_id not in seen_convs:
                seen_convs.add(conv_id)
                await db.execute(
                    "DELETE FROM conversations WHERE id = ?", (conv_id,)
                )
                await db.execute(
                    "INSERT INTO conversations (id, identity, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (conv_id, conv_identity, created_at, created_at),
                )
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, identity, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (msg_id, conv_id, role, msg_identity, content, created_at),
            )
            await db.execute(
                "INSERT INTO message_embeddings (message_id, embedding) VALUES (?, ?)",
                (msg_id, _pack_vector(vec)),
            )
        await db.commit()
    finally:
        await release_db(db)


def _rows_for(count: int, conv_identity: str, conv_id: str, role_pattern="assistant", msg_identity="claude", day="2026-07-01"):
    now_iso = f"{day}T00:00:00"
    out = []
    for i in range(count):
        mid = f"msg-{conv_identity}-{uuid.uuid4().hex[:8]}-{i}"
        out.append((
            mid, conv_id, conv_identity,
            role_pattern, msg_identity,
            f"content number {i} for {conv_identity} long enough to be real",
            now_iso, _vec(hash((conv_identity, i)) % 100000),
        ))
    return out


class BuildCorpusIncrementalMergeTests(unittest.TestCase):
    def _join_rows(self, n, start_rowid=1):
        return [
            (start_rowid + i, f"m{start_rowid + i}", _pack_vector(_vec(i)),
             "claude", "assistant", "claude", "2026-07-01T00:00:00")
            for i in range(n)
        ]

    def test_incremental_merge_matches_full_rebuild(self):
        all_rows = self._join_rows(6)
        full = _build_corpus_dict(all_rows)

        first_half = _build_corpus_dict(all_rows[:3])
        merged = _build_corpus_dict(all_rows[3:], prior=first_half)

        self.assertEqual(merged["count"], full["count"])
        self.assertEqual(merged["ids"], full["ids"])
        np.testing.assert_array_equal(merged["matrix"], full["matrix"])
        np.testing.assert_array_equal(merged["conv_identity"], full["conv_identity"])
        np.testing.assert_array_equal(merged["role"], full["role"])
        np.testing.assert_array_equal(merged["msg_identity"], full["msg_identity"])
        np.testing.assert_array_equal(merged["created_at"], full["created_at"])

    def test_empty_new_rows_returns_prior_unchanged(self):
        prior = _build_corpus_dict(self._join_rows(3))
        result = _build_corpus_dict([], prior=prior)
        self.assertIs(result, prior)


class SearchFilteredCachedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        es._corpus_cache = None

    async def asyncTearDown(self):
        es._corpus_cache = None
        await _wipe_embedding_test_state()

    async def test_identity_mask_excludes_other_identity(self):
        conv_a, conv_b = f"conv-a-{uuid.uuid4().hex[:6]}", f"conv-b-{uuid.uuid4().hex[:6]}"
        rows = _rows_for(3, "Claude", conv_a) + _rows_for(3, "Avery", conv_b)
        await _seed(rows)

        db = await get_db()
        try:
            corpus = await es._get_unfiltered_corpus(db)
            self.assertIsNotNone(corpus)
            self.assertEqual(corpus["count"], 6)

            query_vec = _vec(hash(("Claude", 0)) % 100000)
            results = await _search_filtered_cached(
                db, corpus, query_vec, limit=10, min_similarity=-1.0,
                identity="Claude", speaker=None, after=None, before=None,
            )
        finally:
            await release_db(db)

        self.assertTrue(results)
        for r in results:
            self.assertEqual(r["identity"], "Claude")

    async def test_speaker_user_mask(self):
        conv = f"conv-user-{uuid.uuid4().hex[:6]}"
        rows = (
            _rows_for(2, "Claude", conv, role_pattern="user", msg_identity=None)
            + _rows_for(2, "Claude", conv, role_pattern="assistant", msg_identity="claude")
        )
        await _seed(rows)

        db = await get_db()
        try:
            corpus = await es._get_unfiltered_corpus(db)
            query_vec = _vec(hash(("Claude", 0)) % 100000)
            results = await _search_filtered_cached(
                db, corpus, query_vec, limit=10, min_similarity=-1.0,
                identity=None, speaker="owner", after=None, before=None,
            )
        finally:
            await release_db(db)

        self.assertTrue(results)
        for r in results:
            self.assertEqual(r["role"], "user")

    async def test_date_range_mask(self):
        conv = f"conv-date-{uuid.uuid4().hex[:6]}"
        rows = (
            _rows_for(2, "Claude", conv, day="2026-06-01")
            + _rows_for(2, "Claude", conv, day="2026-07-05")
        )
        await _seed(rows)

        db = await get_db()
        try:
            corpus = await es._get_unfiltered_corpus(db)
            query_vec = _vec(hash(("Claude", 0)) % 100000)
            results = await _search_filtered_cached(
                db, corpus, query_vec, limit=10, min_similarity=-1.0,
                identity=None, speaker=None, after="2026-07-01", before=None,
            )
        finally:
            await release_db(db)

        self.assertTrue(results)
        for r in results:
            self.assertTrue(r["created_at"] >= "2026-07-01")

    async def test_no_matches_returns_empty(self):
        conv = f"conv-empty-{uuid.uuid4().hex[:6]}"
        rows = _rows_for(2, "Claude", conv)
        await _seed(rows)

        db = await get_db()
        try:
            corpus = await es._get_unfiltered_corpus(db)
            query_vec = _vec(999999)
            results = await _search_filtered_cached(
                db, corpus, query_vec, limit=10, min_similarity=-1.0,
                identity="River", speaker=None, after=None, before=None,
            )
        finally:
            await release_db(db)

        self.assertEqual(results, [])


class SemanticSearchFilteredPathUsesCacheTests(unittest.IsolatedAsyncioTestCase):
    """semantic_search() itself should prefer the cached path and only fall
    back to the live JOIN query when the cache is unavailable or errors."""

    async def asyncSetUp(self):
        es._corpus_cache = None

    async def asyncTearDown(self):
        es._corpus_cache = None
        await _wipe_embedding_test_state()

    async def test_filtered_search_calls_cached_path_not_live_fallback(self):
        conv = f"conv-live-{uuid.uuid4().hex[:6]}"
        rows = _rows_for(2, "Claude", conv)
        await _seed(rows)

        db = await get_db()
        try:
            called = {"cached": False, "live_fallback": False}

            async def fake_cached(*_a, **_k):
                called["cached"] = True
                return [{"id": "fake"}]

            orig_execute_fetchall = db.execute_fetchall

            async def spying_execute_fetchall(sql, *a, **k):
                if "e.message_id, e.embedding, m.content" in sql:
                    called["live_fallback"] = True
                return await orig_execute_fetchall(sql, *a, **k)

            from unittest.mock import AsyncMock, patch

            with patch.object(es, "_search_filtered_cached", side_effect=fake_cached), \
                 patch.object(db, "execute_fetchall", side_effect=spying_execute_fetchall), \
                 patch.object(es, "embed_single", AsyncMock(return_value=_vec(0))):
                result = await es.semantic_search(db, "content number", identity="Claude")
        finally:
            await release_db(db)

        self.assertTrue(called["cached"])
        self.assertFalse(called["live_fallback"])
        self.assertEqual(result, [{"id": "fake"}])


if __name__ == "__main__":
    unittest.main()
