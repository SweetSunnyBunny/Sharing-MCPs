"""Semantic embedding service for vector-based message search.

Uses sentence-transformers (all-MiniLM-L6-v2) for local embeddings.
Stores vectors as BLOBs in SQLite and computes cosine similarity in Python.
Auto-embeds new messages via background task.
"""

# ANAM GUIDE: MESSAGE MEANING INDEX (EMBEDDINGS)
# What: Quietly turns every chat message into a "meaning fingerprint" (a vector) stored in the database, so search can find messages by what they MEAN, not just exact words.
# Called by: core/lifespan.py starts the background embedder; api/search.py uses it for semantic search; api/hub.py (Stars tab), services/anam_context_queries.py, claude_api.py, and session_manager.py read from it.
# Edit here when: You want to tune semantic search quality/speed (model, batch size, minimum message length) or investigate the embed-failure counter shown at /api/search/embeddings/status.

import asyncio
import json
import logging
import struct
import time
from typing import Optional

import aiosqlite
import numpy as np

from config import TIMEZONE
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# Lazy-loaded model — only initialized on first use
_model = None
_model_lock = asyncio.Lock()
_EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output dimension
_MODEL_NAME = "all-MiniLM-L6-v2"
_BATCH_SIZE = 64  # messages per embedding batch
_MIN_CONTENT_LENGTH = 10  # skip very short messages

# Embed-failure counter — the semantic index must never rot silently.
# Failures used to vanish into log.debug; now every failure bumps this
# counter and the 1st (and every 25th) failure logs a WARNING so a broken
# model / poisoned batch is visible long before coverage craters. Exposed
# via /api/search/embeddings/status as "embed_failures".
_embed_failure_count = 0


def _record_embed_failure(context: str, detail, error: Exception):
    """Count an embedding failure, warning loudly on the 1st and every 25th."""
    global _embed_failure_count
    _embed_failure_count += 1
    if _embed_failure_count == 1 or _embed_failure_count % 25 == 0:
        log.warning(
            "Embedding failure #%d (%s %s): %s — semantic index may be falling behind",
            _embed_failure_count, context, detail, error,
        )
    else:
        log.debug("Embedding failure #%d (%s %s): %s", _embed_failure_count, context, detail, error)


def get_embed_failure_count() -> int:
    """Total embedding failures since startup (for the status endpoint)."""
    return _embed_failure_count


def _pack_vector(vec: np.ndarray) -> bytes:
    """Pack a float32 numpy array into compact bytes for SQLite BLOB storage."""
    return vec.astype(np.float32).tobytes()


def _unpack_vector(blob: bytes) -> np.ndarray:
    """Unpack a BLOB back into a numpy float32 array."""
    return np.frombuffer(blob, dtype=np.float32)


async def _get_model():
    """Lazy-load the sentence-transformers model."""
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        log.info("Loading embedding model %s...", _MODEL_NAME)
        start = time.monotonic()
        # Run the heavy import + load in a thread to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        _model = await loop.run_in_executor(None, _load_model_sync)
        elapsed = time.monotonic() - start
        log.info("Embedding model loaded in %.1fs", elapsed)
        return _model


def _load_model_sync():
    """Synchronous model loading (runs in executor thread)."""
    from sentence_transformers import SentenceTransformer
    from services.model_load_lock import MODEL_LOAD_LOCK
    # Serialized with other model constructors (e.g. Kokoro warmup) — the HF
    # loader's meta-device init window poisons concurrent torch model builds.
    with MODEL_LOAD_LOCK:
        return SentenceTransformer(_MODEL_NAME)


async def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed a list of texts, returning an (N, 384) float32 array."""
    model = await _get_model()
    loop = asyncio.get_running_loop()
    embeddings = await loop.run_in_executor(
        None, lambda: model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    )
    return embeddings


async def embed_single(text: str) -> np.ndarray:
    """Embed a single text string."""
    result = await embed_texts([text])
    return result[0]


# --- Unfiltered-corpus cache -------------------------------------------------
# An unfiltered semantic_search() scans EVERY embedding row. Re-fetching and
# unpacking all BLOBs on each call grows linearly with total history and runs
# on the chat-turn path, so the unfiltered corpus (ids + float32 matrix +
# precomputed norms) is cached at module level and refreshed incrementally:
# a cheap COUNT/MAX(rowid) watermark check per call, appending only new rows
# when the table grows, full rebuild only if it shrank/changed unexpectedly.
# Full-history recall is preserved — this caches, it never truncates.
_corpus_lock = asyncio.Lock()
_corpus_cache: Optional[dict] = None  # {count, max_rowid, ids, matrix, norms}

# Extra top-K candidates fetched beyond `limit` to absorb embeddings whose
# message row has since been deleted (they drop out of the metadata JOIN).
_CANDIDATE_BUFFER = 20


def _build_corpus_dict(rows, prior: Optional[dict] = None) -> Optional[dict]:
    """Build (or incrementally extend) a corpus cache dict.

    rows: (rowid, message_id, embedding, conv_identity, role, msg_identity,
    created_at) — the join columns feed the #29 filter arrays below. When
    `prior` is given, `rows` are just the NEW rows to append.
    """
    if not rows:
        return prior
    matrix = np.vstack([_unpack_vector(r[2]) for r in rows]).astype(np.float32, copy=False)
    norms = np.linalg.norm(matrix, axis=1)
    # Parallel filter arrays (#29): identity/role/date masks over the SAME
    # cached matrix the unfiltered path already scores, so a filtered search
    # (the identity-scoped default every boy actually uses) rides the fast
    # cache too instead of a live JOIN + per-call BLOB unpack.
    conv_identity = np.array([r[3] or "" for r in rows], dtype=object)
    role = np.array([r[4] or "" for r in rows], dtype=object)
    msg_identity = np.array([(r[5] or "").lower() for r in rows], dtype=object)
    created_at = np.array([r[6] or "" for r in rows], dtype=object)
    ids = [r[1] for r in rows]

    if prior is None:
        return {
            "count": len(rows),
            "max_rowid": rows[-1][0],
            "ids": ids,
            "matrix": matrix,
            "norms": norms,
            "conv_identity": conv_identity,
            "role": role,
            "msg_identity": msg_identity,
            "created_at": created_at,
        }

    return {
        "count": prior["count"] + len(rows),
        "max_rowid": rows[-1][0],
        "ids": prior["ids"] + ids,
        "matrix": np.vstack([prior["matrix"], matrix]),
        "norms": np.concatenate([prior["norms"], norms]),
        "conv_identity": np.concatenate([prior["conv_identity"], conv_identity]),
        "role": np.concatenate([prior["role"], role]),
        "msg_identity": np.concatenate([prior["msg_identity"], msg_identity]),
        "created_at": np.concatenate([prior["created_at"], created_at]),
    }


# JOIN so the corpus carries the metadata #29's filter masks need — the
# extra join cost is paid once per rebuild/append, not once per search.
_CORPUS_SELECT = (
    "SELECT e.rowid, e.message_id, e.embedding, c.identity, m.role, m.identity, m.created_at "
    "FROM message_embeddings e "
    "JOIN messages m ON m.id = e.message_id "
    "JOIN conversations c ON m.conversation_id = c.id "
)


async def _get_unfiltered_corpus(db: aiosqlite.Connection) -> Optional[dict]:
    """Return the cached (ids, matrix, norms) corpus, refreshing if stale."""
    global _corpus_cache

    wm = await db.execute_fetchall("SELECT COUNT(*), MAX(rowid) FROM message_embeddings")
    count = wm[0][0] or 0
    max_rowid = wm[0][1] or 0
    if count == 0:
        return None

    cache = _corpus_cache
    if cache is not None and cache["count"] == count and cache["max_rowid"] == max_rowid:
        return cache  # unchanged — zero blob loading

    async with _corpus_lock:
        # Re-check: another task may have refreshed while we waited.
        cache = _corpus_cache
        if cache is not None and cache["count"] == count and cache["max_rowid"] == max_rowid:
            return cache

        # Grown: fetch only the new rows and append.
        if cache is not None and max_rowid > cache["max_rowid"] and count > cache["count"]:
            try:
                new_rows = await db.execute_fetchall(
                    _CORPUS_SELECT + "WHERE e.rowid > ? ORDER BY e.rowid",
                    (cache["max_rowid"],),
                )
                # Exact only if nothing was deleted since the cache was built.
                if new_rows and len(new_rows) == count - cache["count"]:
                    _corpus_cache = _build_corpus_dict(new_rows, prior=cache)
                    return _corpus_cache
            except Exception as e:
                log.warning("Incremental embedding-cache update failed, rebuilding: %s", e)

        # Shrunk / changed unexpectedly / first load: full rebuild.
        rows = await db.execute_fetchall(_CORPUS_SELECT + "ORDER BY e.rowid")
        _corpus_cache = _build_corpus_dict(rows)
        return _corpus_cache


async def semantic_search(
    db: aiosqlite.Connection,
    query: str,
    identity: str | None = None,
    limit: int = 20,
    min_similarity: float = 0.3,
    speaker: str | None = None,
    after: str | None = None,
    before: str | None = None,
) -> list[dict]:
    """Search messages by semantic similarity to query.

    Filters mirror search_messages(): identity, speaker, after, before.
    Returns results sorted by cosine similarity (highest first).
    """
    if not query or not query.strip():
        return []

    # Embed the query
    query_vec = await embed_single(query.strip())

    if not identity and not speaker and not after and not before:
        # Unfiltered search scans the whole archive — serve it from the
        # in-memory corpus cache (two-phase: score cached vectors, then fetch
        # metadata for just the top-K winners in one small query).
        return await _search_unfiltered(db, query_vec, limit, min_similarity)

    # #29: the default identity-scoped path (what every boy's own recall
    # actually calls) rides the SAME cached corpus, filtered with numpy
    # boolean masks instead of a live JOIN + per-call BLOB unpack. Falls
    # through to the original live-query path only when the cache isn't
    # populated yet or the fast path itself errors — never a regression.
    try:
        corpus = await _get_unfiltered_corpus(db)
    except Exception as e:
        corpus = None
        log.debug("Filtered semantic search: corpus load failed, using live query: %s", e)
    if corpus is not None:
        try:
            return await _search_filtered_cached(
                db, corpus, query_vec, limit, min_similarity,
                identity=identity, speaker=speaker, after=after, before=before,
            )
        except Exception as e:
            log.warning("Filtered cached search failed, falling back to live query: %s", e)

    # Fetch embeddings with SQL-level filters so we don't compute
    # similarity for rows that are excluded anyway.
    sql = """
        SELECT e.message_id, e.embedding, m.content, m.identity, m.role,
               m.created_at, m.conversation_id, c.title, c.identity as conv_identity
        FROM message_embeddings e
        JOIN messages m ON m.id = e.message_id
        JOIN conversations c ON m.conversation_id = c.id
    """
    where: list[str] = []
    params: list = []
    if identity:
        where.append("c.identity = ?")
        params.append(identity)
    if speaker:
        s = speaker.strip().lower()
        if s in ("user", "owner"):
            where.append("m.role = 'user'")
        else:
            where.append("m.role = 'assistant' AND lower(m.identity) = ?")
            params.append(s)
    if after:
        where.append("m.created_at >= ?")
        params.append(after)
    if before:
        where.append("m.created_at < ?")
        params.append(before)
    if where:
        sql += " WHERE " + " AND ".join(where)

    try:
        rows = await db.execute_fetchall(sql, tuple(params))
    except Exception as e:
        log.warning("Semantic search query failed: %s", e)
        return []

    if not rows:
        return []

    # Compute similarities in one vectorized pass. Embeddings are stored
    # normalized (normalize_embeddings=True at encode time), so a single
    # matrix-vector product gives cosine similarity for every row at once —
    # orders of magnitude faster than a per-row Python loop as the archive
    # grows. Norms are guarded anyway so legacy unnormalized rows stay correct.
    matrix = np.vstack([_unpack_vector(row[1]) for row in rows])
    row_norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(query_vec)
    denom = row_norms * query_norm
    denom[denom == 0] = 1.0  # avoid div-by-zero; those rows score 0 anyway
    sims = (matrix @ query_vec) / denom

    scored = []
    for sim, row in zip(sims, rows):
        if sim >= min_similarity:
            msg_id, _blob, content, msg_identity, role, created_at, conv_id, conv_title, conv_identity = row
            scored.append((float(sim), msg_id, content, msg_identity, role, created_at, conv_id, conv_title, conv_identity))

    # Sort by similarity descending
    scored.sort(key=lambda x: x[0], reverse=True)

    return _format_results(scored[:limit])


async def _search_unfiltered(
    db: aiosqlite.Connection,
    query_vec: np.ndarray,
    limit: int,
    min_similarity: float,
) -> list[dict]:
    """Whole-archive semantic search served from the in-memory corpus cache.

    Phase 1 scores every cached vector (no blob loading when the table is
    unchanged); phase 2 fetches content/metadata for only the top candidates
    with a single WHERE id IN (...) query.
    """
    try:
        corpus = await _get_unfiltered_corpus(db)
    except Exception as e:
        log.warning("Semantic search corpus load failed: %s", e)
        return []
    if corpus is None:
        return []

    query_norm = np.linalg.norm(query_vec)
    denom = corpus["norms"] * query_norm  # fresh array — cached norms untouched
    denom[denom == 0] = 1.0  # avoid div-by-zero; those rows score 0 anyway
    sims = (corpus["matrix"] @ query_vec.astype(np.float32, copy=False)) / denom

    hit_idx = np.flatnonzero(sims >= min_similarity)
    if hit_idx.size == 0:
        return []

    # Top candidates (with a small buffer for embeddings whose message row
    # has since been deleted), highest similarity first.
    k = min(hit_idx.size, limit + _CANDIDATE_BUFFER)
    top_idx = hit_idx[np.argsort(sims[hit_idx])[::-1][:k]]
    ids = corpus["ids"]
    candidates = [(float(sims[i]), ids[i]) for i in top_idx]

    placeholders = ",".join("?" * len(candidates))
    try:
        meta_rows = await db.execute_fetchall(
            "SELECT m.id, m.content, m.identity, m.role, m.created_at, "
            "m.conversation_id, c.title, c.identity as conv_identity "
            "FROM messages m "
            "JOIN conversations c ON m.conversation_id = c.id "
            f"WHERE m.id IN ({placeholders})",
            tuple(mid for _sim, mid in candidates),
        )
    except Exception as e:
        log.warning("Semantic search metadata query failed: %s", e)
        return []

    meta = {r[0]: r for r in meta_rows}
    scored = []
    for sim, msg_id in candidates:  # already sorted by similarity desc
        row = meta.get(msg_id)
        if row is None:
            continue  # message deleted after its embedding was cached
        _mid, content, msg_identity, role, created_at, conv_id, conv_title, conv_identity = row
        scored.append((sim, msg_id, content, msg_identity, role, created_at, conv_id, conv_title, conv_identity))
        if len(scored) >= limit:
            break

    return _format_results(scored)


async def _search_filtered_cached(
    db: aiosqlite.Connection,
    corpus: dict,
    query_vec: np.ndarray,
    limit: int,
    min_similarity: float,
    *,
    identity: str | None,
    speaker: str | None,
    after: str | None,
    before: str | None,
) -> list[dict]:
    """Filtered semantic search (#29) served from the cached corpus.

    Same two-phase shape as _search_unfiltered: build a boolean mask over
    the cached parallel arrays, score only the masked rows, then fetch
    fresh content/metadata for just the top-K winners.
    """
    mask = np.ones(corpus["count"], dtype=bool)
    if identity:
        mask &= corpus["conv_identity"] == identity
    if speaker:
        s = speaker.strip().lower()
        if s in ("user", "owner"):
            mask &= corpus["role"] == "user"
        else:
            mask &= (corpus["role"] == "assistant") & (corpus["msg_identity"] == s)
    if after:
        mask &= corpus["created_at"] >= after
    if before:
        mask &= corpus["created_at"] < before

    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []

    query_norm = np.linalg.norm(query_vec)
    q = query_vec.astype(np.float32, copy=False)
    sub_matrix = corpus["matrix"][idx]
    sub_norms = corpus["norms"][idx]
    denom = sub_norms * query_norm
    denom[denom == 0] = 1.0
    sims = (sub_matrix @ q) / denom

    hit_idx = np.flatnonzero(sims >= min_similarity)
    if hit_idx.size == 0:
        return []

    k = min(hit_idx.size, limit + _CANDIDATE_BUFFER)
    top_local = hit_idx[np.argsort(sims[hit_idx])[::-1][:k]]
    top_global = idx[top_local]
    ids = corpus["ids"]
    candidates = [(float(sims[local]), ids[g]) for local, g in zip(top_local, top_global)]

    placeholders = ",".join("?" * len(candidates))
    try:
        meta_rows = await db.execute_fetchall(
            "SELECT m.id, m.content, m.identity, m.role, m.created_at, "
            "m.conversation_id, c.title, c.identity as conv_identity "
            "FROM messages m "
            "JOIN conversations c ON m.conversation_id = c.id "
            f"WHERE m.id IN ({placeholders})",
            tuple(mid for _sim, mid in candidates),
        )
    except Exception as e:
        log.warning("Filtered semantic search metadata query failed: %s", e)
        return []

    meta = {r[0]: r for r in meta_rows}
    scored = []
    for sim, msg_id in candidates:
        row = meta.get(msg_id)
        if row is None:
            continue  # message deleted after its embedding was cached
        _mid, content, msg_identity, role, created_at, conv_id, conv_title, conv_identity = row
        scored.append((sim, msg_id, content, msg_identity, role, created_at, conv_id, conv_title, conv_identity))
        if len(scored) >= limit:
            break

    return _format_results(scored)


def _format_results(scored: list[tuple]) -> list[dict]:
    """Format (sim, msg_id, content, identity, role, created_at, conv_id,
    conv_title, conv_identity) tuples into result dicts."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    results = []
    for sim, msg_id, content, msg_identity, role, created_at, conv_id, conv_title, conv_identity in scored:
        preview = (content or "").strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200].rsplit(" ", 1)[0] + "..."

        try:
            msg_dt = datetime.fromisoformat(created_at).replace(tzinfo=timezone.utc).astimezone(tz)
            secs = (now - msg_dt).total_seconds()
            if secs < 3600:
                ago = f"{max(1, int(secs / 60))}m ago"
            elif secs < 86400:
                ago = f"{secs / 3600:.0f}h ago"
            else:
                ago = f"{int(secs / 86400)}d ago"
            formatted_time = msg_dt.strftime("%b %d, %I:%M %p").lstrip("0")
        except (ValueError, TypeError):
            ago = ""
            formatted_time = created_at or ""

        speaker = "Owner" if role == "user" else (msg_identity or conv_identity or "Unknown")

        results.append({
            "id": msg_id,
            "content_preview": preview,
            "speaker": speaker,
            "role": role,
            "identity": conv_identity,
            "conversation_id": conv_id,
            "conversation_title": conv_title or "Untitled",
            "created_at": created_at,
            "time_ago": ago,
            "formatted_time": formatted_time,
            "similarity": round(sim, 3),
        })

    return results


async def embed_message(db: aiosqlite.Connection, message_id: str, content: str):
    """Embed a single message and store it. Called after saving new messages."""
    if not content or len(content.strip()) < _MIN_CONTENT_LENGTH:
        return

    # Check if already embedded
    rows = await db.execute_fetchall(
        "SELECT 1 FROM message_embeddings WHERE message_id = ?", (message_id,)
    )
    if rows:
        return

    try:
        vec = await embed_single(content.strip()[:2000])  # Cap at 2000 chars for embedding
        blob = _pack_vector(vec)
        await db.execute(
            "INSERT OR IGNORE INTO message_embeddings (message_id, embedding) VALUES (?, ?)",
            (message_id, blob),
        )
        await db.commit()
    except Exception as e:
        _record_embed_failure("message", message_id, e)


async def backfill_embeddings(db: aiosqlite.Connection, batch_size: int = _BATCH_SIZE) -> int:
    """Embed messages that don't have embeddings yet. Returns count embedded."""
    rows = await db.execute_fetchall(
        "SELECT m.id, m.content FROM messages m "
        "LEFT JOIN message_embeddings e ON e.message_id = m.id "
        "WHERE e.message_id IS NULL AND length(m.content) >= ? "
        "ORDER BY m.created_at_epoch DESC "
        "LIMIT ?",
        (_MIN_CONTENT_LENGTH, batch_size),
    )

    if not rows:
        return 0

    ids = [r[0] for r in rows]
    texts = [r[1].strip()[:2000] for r in rows]

    try:
        embeddings = await embed_texts(texts)
    except Exception as e:
        _record_embed_failure("batch", f"{len(ids)} messages", e)
        log.error("Batch embedding failed: %s", e)
        return 0

    count = 0
    for msg_id, vec in zip(ids, embeddings):
        try:
            blob = _pack_vector(vec)
            await db.execute(
                "INSERT OR IGNORE INTO message_embeddings (message_id, embedding) VALUES (?, ?)",
                (msg_id, blob),
            )
            count += 1
        except Exception as e:
            _record_embed_failure("store", msg_id, e)

    await db.commit()
    log.info("Backfilled %d message embeddings", count)
    return count


async def run_backfill_loop(max_batches: int = 100):
    """Run backfill in batches until all messages are embedded or limit reached."""
    from db.database import get_db, release_db

    total = 0
    for i in range(max_batches):
        db = await get_db()
        try:
            count = await backfill_embeddings(db)
        finally:
            await release_db(db)

        total += count
        if count < _BATCH_SIZE:
            break
        # Small delay between batches to not hog resources
        await asyncio.sleep(0.5)

    if total > 0:
        log.info("Embedding backfill complete: %d messages embedded", total)
    return total
