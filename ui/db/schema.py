"""Database schema creation and tracked in-place migrations."""

# ANAM GUIDE: DATABASE TABLES AND MIGRATIONS
# What: Defines every table in anam.db (the SCHEMA text at the top) plus one-time "migrations" that reshape old databases safely.
# Called by: core/lifespan.py runs init_db() once at server startup; many tests import it to build throwaway databases.
# Edit here when: You add a new table or column. Add it to SCHEMA for fresh databases AND write a small _migration_ function
#                 (listed in MIGRATIONS at the bottom) so the existing live database gets the change too.

import json
import logging
from datetime import datetime, timezone

import aiosqlite

log = logging.getLogger(__name__)

from services.session_auth import hash_session_token, looks_like_sha256_hex

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    identity TEXT NOT NULL,
    claude_session_id TEXT,
    title TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER,
    is_active INTEGER DEFAULT 1,
    session_type TEXT DEFAULT 'chat',
    conversation_day TEXT,
    autowake_daily INTEGER DEFAULT 0,
    metadata TEXT,
    platform_chat_id TEXT
);

CREATE TABLE IF NOT EXISTS conversation_provider_sessions (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    session_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER,
    PRIMARY KEY (conversation_id, provider)
);

CREATE TABLE IF NOT EXISTS conversation_participants (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    identity TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, identity)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    identity TEXT,
    content TEXT NOT NULL,
    content_type TEXT DEFAULT 'text',
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS autowake_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cron_hour INTEGER NOT NULL,
    cron_minute INTEGER NOT NULL,
    identity TEXT,
    session_type TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    max_duration_minutes INTEGER DEFAULT 30,
    provider TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    discord_id TEXT NOT NULL,
    discord_username TEXT,
    discord_avatar TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER,
    expires_at TEXT NOT NULL,
    expires_at_epoch INTEGER
);

CREATE TABLE IF NOT EXISTS autowake_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER REFERENCES autowake_schedule(id),
    conversation_id TEXT REFERENCES conversations(id),
    identity TEXT NOT NULL,
    session_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    started_at_epoch INTEGER,
    completed_at TEXT,
    completed_at_epoch INTEGER,
    message_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL,
    fire_at TEXT NOT NULL,
    fire_at_epoch INTEGER,
    context TEXT NOT NULL,
    wake_session INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER,
    fired_at TEXT,
    fired_at_epoch INTEGER,
    retry_count INTEGER DEFAULT 0,
    marker_posted INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    subscription_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_message_map (
    platform TEXT NOT NULL,
    bot_identity TEXT NOT NULL,
    external_message_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    conversation_id TEXT REFERENCES conversations(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (platform, bot_identity, external_message_id, direction)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ts_epoch INTEGER NOT NULL,
    day TEXT NOT NULL,
    identity TEXT,
    conversation_id TEXT,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cost_usd REAL,
    num_turns INTEGER,
    duration_ms INTEGER,
    source TEXT DEFAULT 'claude-code'
);

CREATE TABLE IF NOT EXISTS personal_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    source TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT 'Owner',
    identity TEXT,
    title TEXT NOT NULL,
    body TEXT,
    dedupe_key TEXT,
    payload_json TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER
);

CREATE TABLE IF NOT EXISTS curated_memories (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    identity TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT,
    source_role TEXT,
    source_identity TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER
);

CREATE TABLE IF NOT EXISTS identity_profile_facts (
    id TEXT PRIMARY KEY,
    identity TEXT NOT NULL,
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT,
    confidence TEXT NOT NULL DEFAULT 'strong',
    freshness TEXT NOT NULL DEFAULT 'durable',
    source_memory_id TEXT REFERENCES curated_memories(id) ON DELETE SET NULL,
    source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    source_kind TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'active',
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER,
    last_used_at TEXT,
    last_used_at_epoch INTEGER
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at_epoch);

CREATE INDEX IF NOT EXISTS idx_messages_role_time
    ON messages(role, created_at_epoch);

CREATE INDEX IF NOT EXISTS idx_conversations_active_recent
    ON conversations(is_active, updated_at_epoch DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_stale_cleanup
    ON conversations(is_active, session_type, updated_at_epoch);

CREATE INDEX IF NOT EXISTS idx_conversations_daily_autowake_lookup
    ON conversations(identity, session_type, conversation_day, is_active, autowake_daily);

CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_autowake_daily_chat
    ON conversations(identity, session_type, conversation_day)
    WHERE autowake_daily = 1 AND session_type = 'chat' AND conversation_day IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_participants_identity
    ON conversation_participants(identity, conversation_id);

CREATE INDEX IF NOT EXISTS idx_sessions_expires
    ON sessions(expires_at_epoch);

CREATE INDEX IF NOT EXISTS idx_timers_pending
    ON timers(status, fire_at_epoch);

CREATE INDEX IF NOT EXISTS idx_messages_bookmarked_recent
    ON messages(json_extract(metadata, '$.bookmarked'), created_at_epoch DESC);

CREATE INDEX IF NOT EXISTS idx_conversations_platform_chat
    ON conversations(platform_chat_id);

CREATE INDEX IF NOT EXISTS idx_platform_message_lookup
    ON platform_message_map(platform, bot_identity, conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_timeline_recent
    ON personal_timeline(entry_date, created_at_epoch DESC);

CREATE INDEX IF NOT EXISTS idx_usage_log_day
    ON usage_log(day, identity);

CREATE INDEX IF NOT EXISTS idx_timeline_subject_type
    ON personal_timeline(subject, entry_type, created_at_epoch DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_timeline_dedupe
    ON personal_timeline(dedupe_key)
    WHERE dedupe_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_memories_identity_recent
    ON curated_memories(identity, status, created_at_epoch DESC);

CREATE INDEX IF NOT EXISTS idx_memories_message
    ON curated_memories(message_id);

CREATE INDEX IF NOT EXISTS idx_profile_facts_identity_recent
    ON identity_profile_facts(identity, status, updated_at_epoch DESC);

CREATE INDEX IF NOT EXISTS idx_profile_facts_source_memory
    ON identity_profile_facts(source_memory_id);

CREATE INDEX IF NOT EXISTS idx_profile_facts_category
    ON identity_profile_facts(identity, category, status);

CREATE TABLE IF NOT EXISTS emotional_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    identity TEXT NOT NULL,
    tone TEXT NOT NULL,
    energy TEXT,
    arc TEXT,
    markers TEXT,
    summary TEXT NOT NULL,
    message_count INTEGER,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER
);

CREATE INDEX IF NOT EXISTS idx_emotional_snapshots_identity_recent
    ON emotional_snapshots(identity, created_at_epoch DESC);

CREATE TABLE IF NOT EXISTS tool_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL,
    conversation_id TEXT,
    source TEXT NOT NULL DEFAULT 'chat',
    tool_name TEXT NOT NULL,
    input_summary TEXT,
    output_head TEXT,
    created_at_epoch INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_audit_identity_time
    ON tool_audit(identity, created_at_epoch);

CREATE TABLE IF NOT EXISTS identity_carries (
    identity TEXT NOT NULL,
    carry_date TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    PRIMARY KEY (identity, carry_date)
);

CREATE TABLE IF NOT EXISTS voice_calibration_samples (
    audio_id TEXT PRIMARY KEY,
    audio_filename TEXT NOT NULL,
    label TEXT NOT NULL,
    target_identity TEXT,
    conversation_id TEXT,
    transcript TEXT,
    prediction_json TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_voice_calibration_label_time
    ON voice_calibration_samples(label, created_at_epoch DESC);

CREATE INDEX IF NOT EXISTS idx_timeline_subject_date ON personal_timeline(subject, entry_date);

CREATE INDEX IF NOT EXISTS idx_conversations_identity_updated ON conversations(identity, updated_at_epoch);

CREATE TRIGGER IF NOT EXISTS trg_conversations_insert_participant
AFTER INSERT ON conversations
BEGIN
    INSERT OR IGNORE INTO conversation_participants (conversation_id, identity, added_at)
    SELECT NEW.id, NEW.identity, COALESCE(NEW.created_at, datetime('now'))
    WHERE TRIM(COALESCE(NEW.identity, '')) <> '';
END;

CREATE TRIGGER IF NOT EXISTS trg_conversations_update_identity_participant
AFTER UPDATE OF identity ON conversations
BEGIN
    INSERT OR IGNORE INTO conversation_participants (conversation_id, identity, added_at)
    SELECT NEW.id, NEW.identity, COALESCE(NEW.updated_at, NEW.created_at, datetime('now'))
    WHERE TRIM(COALESCE(NEW.identity, '')) <> '';
END;

CREATE TRIGGER IF NOT EXISTS trg_cp_repair_empty
AFTER DELETE ON conversation_participants
WHEN EXISTS(SELECT 1 FROM conversations WHERE id = OLD.conversation_id)
  AND NOT EXISTS(
    SELECT 1 FROM conversation_participants
    WHERE conversation_id = OLD.conversation_id
  )
BEGIN
    INSERT OR IGNORE INTO conversation_participants (conversation_id, identity, added_at)
    SELECT c.id, c.identity, COALESCE(c.updated_at, c.created_at, datetime('now'))
    FROM conversations c
    WHERE c.id = OLD.conversation_id
      AND TRIM(COALESCE(c.identity, '')) <> '';
END;

CREATE TRIGGER IF NOT EXISTS trg_cp_repair_primary
AFTER DELETE ON conversation_participants
WHEN EXISTS(SELECT 1 FROM conversations WHERE id = OLD.conversation_id)
  AND NOT EXISTS(
    SELECT 1
    FROM conversation_participants cp
    JOIN conversations c ON c.id = cp.conversation_id
    WHERE cp.conversation_id = OLD.conversation_id
      AND cp.identity = c.identity
  )
BEGIN
    INSERT OR IGNORE INTO conversation_participants (conversation_id, identity, added_at)
    SELECT c.id, c.identity, COALESCE(c.updated_at, c.created_at, datetime('now'))
    FROM conversations c
    WHERE c.id = OLD.conversation_id
      AND TRIM(COALESCE(c.identity, '')) <> '';
END;

CREATE TABLE IF NOT EXISTS story_worlds (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    story_identity TEXT NOT NULL DEFAULT 'Bakugou',
    story_branch TEXT NOT NULL DEFAULT '',
    source_package TEXT,
    fictional_now TEXT,
    clock_label TEXT NOT NULL DEFAULT '',
    posting_enabled INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS story_feed_profiles (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    handle TEXT NOT NULL,
    display_name TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT 'character',
    bio TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    header_url TEXT NOT NULL DEFAULT '',
    accent_color TEXT NOT NULL DEFAULT '#D9485F',
    is_user_controlled INTEGER NOT NULL DEFAULT 0,
    is_verified INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    prompt_notes TEXT NOT NULL DEFAULT '',
    posting_style TEXT NOT NULL DEFAULT '',
    knowledge_json TEXT NOT NULL DEFAULT '[]',
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER NOT NULL,
    UNIQUE(world_id, handle COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS story_feed_relationships (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    from_profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    to_profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL DEFAULT '',
    public_summary TEXT NOT NULL DEFAULT '',
    private_context TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    visibility TEXT NOT NULL DEFAULT 'private',
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER NOT NULL,
    UNIQUE(world_id, from_profile_id, to_profile_id)
);

CREATE TABLE IF NOT EXISTS story_feed_ai_runs (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    trigger_kind TEXT NOT NULL,
    fictional_window TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    provider TEXT,
    model TEXT,
    checkpoint_hash TEXT,
    request_json TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    completed_at TEXT,
    completed_at_epoch INTEGER
);

CREATE TABLE IF NOT EXISTS story_feed_posts (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    author_profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    body TEXT NOT NULL DEFAULT '',
    post_type TEXT NOT NULL DEFAULT 'post',
    parent_post_id TEXT REFERENCES story_feed_posts(id) ON DELETE SET NULL,
    quote_post_id TEXT REFERENCES story_feed_posts(id) ON DELETE SET NULL,
    fictional_at TEXT,
    canon_level TEXT NOT NULL DEFAULT 'ambient',
    timeline_order INTEGER,
    canon_status TEXT NOT NULL DEFAULT 'approved',
    origin TEXT NOT NULL DEFAULT 'human',
    ai_run_id TEXT REFERENCES story_feed_ai_runs(id) ON DELETE SET NULL,
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS story_feed_submissions (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    post_id TEXT NOT NULL REFERENCES story_feed_posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS story_feed_bookmarks (
    post_id TEXT NOT NULL REFERENCES story_feed_posts(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    PRIMARY KEY(profile_id, post_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_story_unique_repost
ON story_feed_posts(author_profile_id, quote_post_id) WHERE post_type = 'repost';

CREATE TABLE IF NOT EXISTS story_feed_media (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL REFERENCES story_feed_posts(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL DEFAULT 'image',
    url TEXT NOT NULL,
    alt_text TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS story_feed_photo_jobs (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    idea TEXT NOT NULL DEFAULT '',
    plan_json TEXT,
    image_url TEXT,
    post_id TEXT REFERENCES story_feed_posts(id) ON DELETE SET NULL,
    error TEXT NOT NULL DEFAULT '',
    automatic INTEGER NOT NULL DEFAULT 0,
    created_at_epoch INTEGER NOT NULL,
    updated_at_epoch INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_story_photo_jobs_world_time
    ON story_feed_photo_jobs(world_id, created_at_epoch DESC);

CREATE TABLE IF NOT EXISTS story_feed_post_hashtags (
    post_id TEXT NOT NULL REFERENCES story_feed_posts(id) ON DELETE CASCADE,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    tag TEXT NOT NULL COLLATE NOCASE,
    display_tag TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    PRIMARY KEY (post_id, tag)
);

CREATE TABLE IF NOT EXISTS story_feed_mentions (
    post_id TEXT NOT NULL REFERENCES story_feed_posts(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    handle TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    PRIMARY KEY (post_id, profile_id)
);

CREATE TABLE IF NOT EXISTS story_feed_reactions (
    post_id TEXT NOT NULL REFERENCES story_feed_posts(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'like',
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    PRIMARY KEY (post_id, profile_id, kind)
);

CREATE TABLE IF NOT EXISTS story_feed_follows (
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    follower_profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    followed_profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    PRIMARY KEY (world_id, follower_profile_id, followed_profile_id)
);

CREATE TABLE IF NOT EXISTS story_feed_notifications (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    target_profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    actor_profile_id TEXT REFERENCES story_feed_profiles(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    post_id TEXT REFERENCES story_feed_posts(id) ON DELETE CASCADE,
    is_read INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS story_feed_character_memories (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    other_profile_id TEXT REFERENCES story_feed_profiles(id) ON DELETE SET NULL,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'private',
    fictional_at TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    UNIQUE(profile_id, source_kind, source_id)
);

CREATE TABLE IF NOT EXISTS story_feed_dm_threads (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    participant_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER NOT NULL,
    UNIQUE(world_id, participant_key)
);

CREATE TABLE IF NOT EXISTS story_feed_dm_participants (
    thread_id TEXT NOT NULL REFERENCES story_feed_dm_threads(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    last_read_message_rowid INTEGER NOT NULL DEFAULT 0,
    joined_at TEXT NOT NULL,
    joined_at_epoch INTEGER NOT NULL,
    PRIMARY KEY(thread_id, profile_id)
);

CREATE TABLE IF NOT EXISTS story_feed_dm_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES story_feed_dm_threads(id) ON DELETE CASCADE,
    sender_profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'human',
    metadata TEXT,
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS story_feed_dm_reply_jobs (
    id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES story_worlds(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL REFERENCES story_feed_dm_threads(id) ON DELETE CASCADE,
    source_message_id TEXT NOT NULL REFERENCES story_feed_dm_messages(id) ON DELETE CASCADE,
    responder_profile_id TEXT NOT NULL REFERENCES story_feed_profiles(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    not_before_epoch INTEGER NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_at_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    updated_at_epoch INTEGER NOT NULL,
    UNIQUE(source_message_id, responder_profile_id)
);

CREATE INDEX IF NOT EXISTS idx_story_profiles_world_handle
    ON story_feed_profiles(world_id, handle COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_story_relationships_world_from
    ON story_feed_relationships(world_id, from_profile_id);
CREATE INDEX IF NOT EXISTS idx_story_posts_world_time
    ON story_feed_posts(world_id, created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_story_posts_author_time
    ON story_feed_posts(author_profile_id, created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_story_posts_parent
    ON story_feed_posts(parent_post_id, created_at_epoch);
CREATE INDEX IF NOT EXISTS idx_story_media_post_order
    ON story_feed_media(post_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_story_hashtags_world_tag_time
    ON story_feed_post_hashtags(world_id, tag, created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_story_mentions_profile_time
    ON story_feed_mentions(profile_id, created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_story_notifications_target_time
    ON story_feed_notifications(target_profile_id, is_read, created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_story_memories_profile_time
    ON story_feed_character_memories(profile_id, created_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_story_memories_world_source
    ON story_feed_character_memories(world_id, source_kind, source_id);
CREATE INDEX IF NOT EXISTS idx_story_dm_threads_world_time
    ON story_feed_dm_threads(world_id, updated_at_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_story_dm_messages_thread_time
    ON story_feed_dm_messages(thread_id, created_at_epoch);
CREATE INDEX IF NOT EXISTS idx_story_dm_jobs_ready
    ON story_feed_dm_reply_jobs(status, not_before_epoch);
CREATE INDEX IF NOT EXISTS idx_story_ai_runs_world_time
    ON story_feed_ai_runs(world_id, created_at_epoch DESC);
"""


def _parse_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


async def _ensure_migrations_table(db: aiosqlite.Connection):
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "name TEXT PRIMARY KEY, "
        "applied_at TEXT NOT NULL"
        ")"
    )
    await db.commit()


async def _is_migration_applied(db: aiosqlite.Connection, name: str) -> bool:
    rows = await db.execute_fetchall(
        "SELECT 1 FROM schema_migrations WHERE name = ?",
        (name,),
    )
    return bool(rows)


async def _apply_migration(db: aiosqlite.Connection, name: str, fn):
    if await _is_migration_applied(db, name):
        return
    await fn(db)
    await db.execute(
        "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (name, datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()


async def _migration_ensure_legacy_columns(db: aiosqlite.Connection):
    migrations = {
        "conversations": [
            "metadata TEXT",
            "platform_chat_id TEXT",
            "created_at_epoch INTEGER",
            "updated_at_epoch INTEGER",
            "conversation_day TEXT",
            "autowake_daily INTEGER DEFAULT 0",
        ],
        "messages": [
            "content_type TEXT DEFAULT 'text'",
            "metadata TEXT",
            "created_at_epoch INTEGER",
        ],
        "sessions": [
            "token_hash TEXT",
            "created_at_epoch INTEGER",
            "expires_at_epoch INTEGER",
        ],
        "autowake_log": [
            "conversation_id TEXT",
            "started_at_epoch INTEGER",
            "completed_at_epoch INTEGER",
        ],
        "timers": [
            "fire_at_epoch INTEGER",
            "created_at_epoch INTEGER",
            "fired_at_epoch INTEGER",
            "retry_count INTEGER DEFAULT 0",
        ],
    }

    for table, columns in migrations.items():
        for col in columns:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
            except Exception as e:
                log.debug("Column may already exist (%s.%s): %s", table, col.split()[0], e)


async def _migration_sessions_to_token_hash(db: aiosqlite.Connection):
    table_info = await db.execute_fetchall("PRAGMA table_info(sessions)")
    if not table_info:
        return

    columns = {row[1] for row in table_info}
    if "token" not in columns:
        return

    def pick(col: str) -> str:
        return col if col in columns else f"NULL AS {col}"

    await db.execute("DROP TABLE IF EXISTS sessions_new")
    await db.execute(
        "CREATE TABLE sessions_new ("
        "token_hash TEXT PRIMARY KEY, "
        "discord_id TEXT NOT NULL, "
        "discord_username TEXT, "
        "discord_avatar TEXT, "
        "created_at TEXT NOT NULL, "
        "created_at_epoch INTEGER, "
        "expires_at TEXT NOT NULL, "
        "expires_at_epoch INTEGER"
        ")"
    )

    rows = await db.execute_fetchall(
        "SELECT "
        f"{pick('token')}, "
        f"{pick('token_hash')}, "
        f"{pick('discord_id')}, "
        f"{pick('discord_username')}, "
        f"{pick('discord_avatar')}, "
        f"{pick('created_at')}, "
        f"{pick('created_at_epoch')}, "
        f"{pick('expires_at')}, "
        f"{pick('expires_at_epoch')} "
        "FROM sessions"
    )

    for (
        legacy_token,
        legacy_token_hash,
        discord_id,
        discord_username,
        discord_avatar,
        created_at,
        created_at_epoch,
        expires_at,
        expires_at_epoch,
    ) in rows:
        token_hash = str(legacy_token_hash or "")
        if not token_hash or not looks_like_sha256_hex(token_hash):
            token_hash = hash_session_token(str(legacy_token or token_hash))

        if created_at_epoch is None:
            created_at_epoch = _parse_epoch(created_at)
        if expires_at_epoch is None:
            expires_at_epoch = _parse_epoch(expires_at)

        await db.execute(
            "INSERT OR REPLACE INTO sessions_new "
            "(token_hash, discord_id, discord_username, discord_avatar, "
            "created_at, created_at_epoch, expires_at, expires_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                token_hash,
                discord_id,
                discord_username,
                discord_avatar,
                created_at,
                created_at_epoch,
                expires_at,
                expires_at_epoch,
            ),
        )

    await db.execute("DROP TABLE sessions")
    await db.execute("ALTER TABLE sessions_new RENAME TO sessions")


async def _migration_backfill_epochs(db: aiosqlite.Connection):
    await db.executescript(
        """
        UPDATE conversations
        SET created_at_epoch = CAST(strftime('%s', created_at) AS INTEGER)
        WHERE created_at_epoch IS NULL AND created_at IS NOT NULL;

        UPDATE conversations
        SET updated_at_epoch = CAST(strftime('%s', updated_at) AS INTEGER)
        WHERE updated_at_epoch IS NULL AND updated_at IS NOT NULL;

        UPDATE messages
        SET created_at_epoch = CAST(strftime('%s', created_at) AS INTEGER)
        WHERE created_at_epoch IS NULL AND created_at IS NOT NULL;

        UPDATE sessions
        SET created_at_epoch = CAST(strftime('%s', created_at) AS INTEGER)
        WHERE created_at_epoch IS NULL AND created_at IS NOT NULL;

        UPDATE sessions
        SET expires_at_epoch = CAST(strftime('%s', expires_at) AS INTEGER)
        WHERE expires_at_epoch IS NULL AND expires_at IS NOT NULL;

        UPDATE autowake_log
        SET started_at_epoch = CAST(strftime('%s', started_at) AS INTEGER)
        WHERE started_at_epoch IS NULL AND started_at IS NOT NULL;

        UPDATE autowake_log
        SET completed_at_epoch = CAST(strftime('%s', completed_at) AS INTEGER)
        WHERE completed_at_epoch IS NULL AND completed_at IS NOT NULL;

        UPDATE timers
        SET fire_at_epoch = CAST(strftime('%s', fire_at) AS INTEGER)
        WHERE fire_at_epoch IS NULL AND fire_at IS NOT NULL;

        UPDATE timers
        SET created_at_epoch = CAST(strftime('%s', created_at) AS INTEGER)
        WHERE created_at_epoch IS NULL AND created_at IS NOT NULL;

        UPDATE timers
        SET fired_at_epoch = CAST(strftime('%s', fired_at) AS INTEGER)
        WHERE fired_at_epoch IS NULL AND fired_at IS NOT NULL;
        """
    )

    fallback_specs = [
        ("conversations", "id", "created_at", "created_at_epoch"),
        ("conversations", "id", "updated_at", "updated_at_epoch"),
        ("messages", "id", "created_at", "created_at_epoch"),
        ("sessions", "token_hash", "created_at", "created_at_epoch"),
        ("sessions", "token_hash", "expires_at", "expires_at_epoch"),
        ("autowake_log", "id", "started_at", "started_at_epoch"),
        ("autowake_log", "id", "completed_at", "completed_at_epoch"),
        ("timers", "id", "fire_at", "fire_at_epoch"),
        ("timers", "id", "created_at", "created_at_epoch"),
        ("timers", "id", "fired_at", "fired_at_epoch"),
    ]
    for table, pk_col, iso_col, epoch_col in fallback_specs:
        rows = await db.execute_fetchall(
            f"SELECT {pk_col}, {iso_col} FROM {table} "
            f"WHERE {epoch_col} IS NULL AND {iso_col} IS NOT NULL"
        )
        for pk, iso_value in rows:
            epoch_value = _parse_epoch(iso_value)
            if epoch_value is None:
                continue
            await db.execute(
                f"UPDATE {table} SET {epoch_col} = ? WHERE {pk_col} = ?",
                (epoch_value, pk),
            )


async def _migration_backfill_conversation_participants(db: aiosqlite.Connection):
    rows = await db.execute_fetchall(
        "SELECT id, identity, created_at FROM conversations"
    )
    for conv_id, identity, created_at in rows:
        raw_identity = (identity or "").strip()
        participants: list[str] = []
        if raw_identity:
            participants = [
                part.strip() for part in raw_identity.split(",") if part.strip()
            ]
        if not participants:
            continue

        primary_identity = participants[0]
        if raw_identity != primary_identity:
            await db.execute(
                "UPDATE conversations SET identity = ? WHERE id = ?",
                (primary_identity, conv_id),
            )

        added_at = created_at or datetime.now(timezone.utc).isoformat()
        seen: set[str] = set()
        for participant in participants:
            if participant in seen:
                continue
            seen.add(participant)
            await db.execute(
                "INSERT OR IGNORE INTO conversation_participants "
                "(conversation_id, identity, added_at) VALUES (?, ?, ?)",
                (conv_id, participant, added_at),
            )


async def _migration_normalize_session_hashes(db: aiosqlite.Connection):
    rows = await db.execute_fetchall("SELECT token_hash FROM sessions")
    for (token_hash,) in rows:
        token_hash_str = str(token_hash or "")
        if looks_like_sha256_hex(token_hash_str):
            continue
        normalized_hash = hash_session_token(token_hash_str)
        await db.execute(
            "UPDATE sessions SET token_hash = ? WHERE token_hash = ?",
            (normalized_hash, token_hash_str),
        )


async def _migration_add_daily_autowake_columns(db: aiosqlite.Connection):
    for statement in (
        "ALTER TABLE conversations ADD COLUMN conversation_day TEXT",
        "ALTER TABLE conversations ADD COLUMN autowake_daily INTEGER DEFAULT 0",
        "ALTER TABLE autowake_log ADD COLUMN conversation_id TEXT",
    ):
        try:
            await db.execute(statement)
        except Exception as e:
            log.debug("Migration statement may have already run: %s", e)

    await db.execute(
        "UPDATE conversations SET autowake_daily = 0 WHERE autowake_daily IS NULL"
    )


async def _migration_add_personal_state_tables(db: aiosqlite.Connection):
    for statement in (
        "CREATE TABLE IF NOT EXISTS personal_timeline ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "entry_date TEXT NOT NULL, "
        "entry_type TEXT NOT NULL, "
        "source TEXT NOT NULL, "
        "subject TEXT NOT NULL DEFAULT 'Owner', "
        "identity TEXT, "
        "title TEXT NOT NULL, "
        "body TEXT, "
        "dedupe_key TEXT, "
        "payload_json TEXT, "
        "metadata TEXT, "
        "created_at TEXT NOT NULL, "
        "created_at_epoch INTEGER, "
        "updated_at TEXT NOT NULL, "
        "updated_at_epoch INTEGER"
        ")",
        "CREATE TABLE IF NOT EXISTS curated_memories ("
        "id TEXT PRIMARY KEY, "
        "conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL, "
        "message_id TEXT REFERENCES messages(id) ON DELETE SET NULL, "
        "identity TEXT NOT NULL, "
        "memory_type TEXT NOT NULL, "
        "summary TEXT NOT NULL, "
        "detail TEXT, "
        "source_role TEXT, "
        "source_identity TEXT, "
        "status TEXT NOT NULL DEFAULT 'active', "
        "metadata TEXT, "
        "created_at TEXT NOT NULL, "
        "created_at_epoch INTEGER, "
        "updated_at TEXT NOT NULL, "
        "updated_at_epoch INTEGER"
        ")",
        "CREATE INDEX IF NOT EXISTS idx_timeline_recent "
        "ON personal_timeline(entry_date, created_at_epoch DESC)",
        "CREATE INDEX IF NOT EXISTS idx_timeline_subject_type "
        "ON personal_timeline(subject, entry_type, created_at_epoch DESC)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_timeline_dedupe "
        "ON personal_timeline(dedupe_key) WHERE dedupe_key IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_memories_identity_recent "
        "ON curated_memories(identity, status, created_at_epoch DESC)",
        "CREATE INDEX IF NOT EXISTS idx_memories_message "
        "ON curated_memories(message_id)",
    ):
        await db.execute(statement)


async def _migration_add_identity_profile_facts(db: aiosqlite.Connection):
    for statement in (
        "CREATE TABLE IF NOT EXISTS identity_profile_facts ("
        "id TEXT PRIMARY KEY, "
        "identity TEXT NOT NULL, "
        "category TEXT NOT NULL, "
        "summary TEXT NOT NULL, "
        "detail TEXT, "
        "confidence TEXT NOT NULL DEFAULT 'strong', "
        "freshness TEXT NOT NULL DEFAULT 'durable', "
        "source_memory_id TEXT REFERENCES curated_memories(id) ON DELETE SET NULL, "
        "source_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL, "
        "source_kind TEXT NOT NULL DEFAULT 'manual', "
        "status TEXT NOT NULL DEFAULT 'active', "
        "metadata TEXT, "
        "created_at TEXT NOT NULL, "
        "created_at_epoch INTEGER, "
        "updated_at TEXT NOT NULL, "
        "updated_at_epoch INTEGER, "
        "last_used_at TEXT, "
        "last_used_at_epoch INTEGER"
        ")",
        "CREATE INDEX IF NOT EXISTS idx_profile_facts_identity_recent "
        "ON identity_profile_facts(identity, status, updated_at_epoch DESC)",
        "CREATE INDEX IF NOT EXISTS idx_profile_facts_source_memory "
        "ON identity_profile_facts(source_memory_id)",
        "CREATE INDEX IF NOT EXISTS idx_profile_facts_category "
        "ON identity_profile_facts(identity, category, status)",
    ):
        await db.execute(statement)


async def _migration_add_message_fts(db: aiosqlite.Connection):
    """Create FTS5 virtual table for full-text search on chat messages."""
    await db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
        "content, "
        "content='messages', "
        "content_rowid='rowid'"
        ")"
    )
    # Populate FTS index from existing messages
    await db.execute(
        "INSERT OR IGNORE INTO messages_fts(rowid, content) "
        "SELECT rowid, content FROM messages"
    )
    # Triggers to keep FTS in sync with messages table
    await db.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_messages_fts_insert "
        "AFTER INSERT ON messages BEGIN "
        "INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); "
        "END"
    )
    await db.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_messages_fts_delete "
        "AFTER DELETE ON messages BEGIN "
        "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content); "
        "END"
    )
    await db.execute(
        "CREATE TRIGGER IF NOT EXISTS trg_messages_fts_update "
        "AFTER UPDATE OF content ON messages BEGIN "
        "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content); "
        "INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content); "
        "END"
    )


async def _migration_add_triggers_table(db: aiosqlite.Connection):
    """Add the triggers table for impulses and watchers."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            identity TEXT NOT NULL,
            condition_json TEXT NOT NULL,
            action_type TEXT DEFAULT 'autowake',
            prompt TEXT,
            session_type TEXT DEFAULT 'custom',
            max_duration_minutes INTEGER DEFAULT 15,
            enabled INTEGER DEFAULT 1,
            cooldown_minutes INTEGER DEFAULT 30,
            last_fired_at TEXT,
            last_fired_at_epoch INTEGER,
            fire_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            created_at_epoch INTEGER
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_triggers_enabled "
        "ON triggers(enabled, trigger_type)"
    )


async def _migration_force_cli_bypass_approvals(db: aiosqlite.Connection):
    provider_rows = await db.execute_fetchall(
        "SELECT value FROM settings WHERE key = ?",
        ("llm_provider",),
    )
    provider = provider_rows[0][0] if provider_rows else ""
    if provider not in {"claude-code", "codex"}:
        return

    config_rows = await db.execute_fetchall(
        "SELECT value FROM settings WHERE key = ?",
        ("llm_provider_config",),
    )
    raw = config_rows[0][0] if config_rows else "{}"
    try:
        config = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        config = {}

    changed = False
    if provider == "claude-code" and config.get("permission_mode") != "bypassPermissions":
        config["permission_mode"] = "bypassPermissions"
        changed = True
    if provider == "codex" and config.get("bypass_approvals") is not True:
        config["bypass_approvals"] = True
        changed = True

    if not changed:
        return

    value = json.dumps(config)
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?",
        ("llm_provider_config", value, now, value, now),
    )


async def _migration_add_message_embeddings(db: aiosqlite.Connection):
    """Create table for semantic search embeddings (384-dim float32 BLOBs)."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS message_embeddings (
            message_id TEXT PRIMARY KEY,
            embedding BLOB NOT NULL,
            FOREIGN KEY (message_id) REFERENCES messages(id)
        )
    """)


async def _migration_add_pulses_table(db: aiosqlite.Connection):
    """Add persistent pulse definitions for user-configurable periodic checks."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS pulses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            identity TEXT,
            interval_seconds INTEGER NOT NULL DEFAULT 300,
            condition_json TEXT,
            action_type TEXT DEFAULT 'autowake',
            prompt TEXT,
            session_type TEXT DEFAULT 'custom',
            max_duration_minutes INTEGER DEFAULT 5,
            enabled INTEGER DEFAULT 1,
            last_fired_at TEXT,
            last_fired_at_epoch INTEGER,
            fire_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            created_at_epoch INTEGER
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pulses_enabled ON pulses(enabled)"
    )


async def _migration_add_schedule_extensions(db: aiosqlite.Connection):
    """Add custom_prompt and enabled_condition to autowake_schedule."""
    for col, col_type in [
        ("custom_prompt", "TEXT"),
        ("enabled_condition", "TEXT"),
    ]:
        try:
            await db.execute(
                f"ALTER TABLE autowake_schedule ADD COLUMN {col} {col_type}"
            )
        except Exception:
            pass  # Column already exists


async def _migration_add_schedule_effort(db: aiosqlite.Connection):
    """#27: per-schedule effort override — pack night xhigh, glance-wakes
    low, without touching the global CLAUDE_EFFORT setting everything else
    still uses. NULL/empty means "use the global default", same as before
    this column existed."""
    try:
        await db.execute("ALTER TABLE autowake_schedule ADD COLUMN effort TEXT")
    except Exception:
        pass  # Column already exists


async def _migration_add_schedule_model(db: aiosqlite.Connection):
    """Per-schedule model override — e.g. River's chapter reviews on Fable 5
    while everything else stays on the global autowake model. Accepts full
    model ids ("claude-fable-5") or CLI aliases ("fable", "opus", "sonnet",
    "haiku"). NULL/empty means "use the global autowake/interactive model",
    same as before this column existed. Mirrors the per-schedule effort
    column (#27) exactly."""
    try:
        await db.execute("ALTER TABLE autowake_schedule ADD COLUMN model TEXT")
    except Exception:
        pass  # Column already exists


async def _migration_add_schedule_provider(db: aiosqlite.Connection):
    """Add an optional provider override for one autonomous schedule."""
    try:
        await db.execute("ALTER TABLE autowake_schedule ADD COLUMN provider TEXT")
    except Exception:
        pass  # Column already exists


async def _migration_archive_legacy_pack_night(db: aiosqlite.Connection):
    """Archive ghost pack-night conversations from the failed orchestrator.

    Avery's earlier orchestrator created per-boy `session_type='pack-night'`
    rows directly in Anam (lowercase identity names, no platform_chat_id),
    each containing the broken transcript from last night. They aren't the
    new shared room and they shouldn't share its sidebar slot. Move them to
    a distinct session_type and mark them inactive so the new singleton --
    which has platform_chat_id='pack-night:home' -- is the only Pack Night
    card visible going forward. The messages themselves stay in the DB if
    Owner ever wants to look back.
    """
    await db.execute(
        "UPDATE conversations "
        "SET session_type = 'pack-night-legacy', is_active = 0 "
        "WHERE session_type = 'pack-night' "
        "AND (platform_chat_id IS NULL OR platform_chat_id != 'pack-night:home')"
    )


async def _migration_add_emotional_snapshots(db: aiosqlite.Connection):
    """Add emotional_snapshots table for periodic tone capture."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS emotional_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            identity TEXT NOT NULL,
            tone TEXT NOT NULL,
            energy TEXT,
            arc TEXT,
            markers TEXT,
            summary TEXT NOT NULL,
            message_count INTEGER,
            created_at TEXT NOT NULL,
            created_at_epoch INTEGER
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_emotional_snapshots_identity_recent "
        "ON emotional_snapshots(identity, created_at_epoch DESC)"
    )


async def _migration_drop_duplicate_indexes(db: aiosqlite.Connection):
    """Drop indexes that byte-for-byte duplicate earlier ones in SCHEMA.

    idx_messages_conv_epoch duplicated idx_messages_conversation
    (messages(conversation_id, created_at_epoch)) and
    idx_profile_facts_identity_status duplicated
    idx_profile_facts_identity_recent
    (identity_profile_facts(identity, status, updated_at_epoch)).
    Their CREATE statements were removed from SCHEMA; this sheds the
    redundant copies from databases that already built them.
    """
    await db.execute("DROP INDEX IF EXISTS idx_messages_conv_epoch")
    await db.execute("DROP INDEX IF EXISTS idx_profile_facts_identity_status")


async def _migration_add_busy_delivery_columns(db: aiosqlite.Connection):
    """Busy-boy delivery: trigger waiting queue + timer busy-marker guard.

    triggers.status ('idle' | 'waiting'): a matched trigger whose identity was
    busy is parked as 'waiting' and fired on a later evaluator tick once the
    identity frees — without re-evaluating conditions (the matched moment was
    real; presence events age out of the eval window).
    triggers.waiting_since_epoch: when it entered the waiting state.
    timers.marker_posted: set to 1 once the instant busy-marker message/push
    for a due-but-busy timer has been delivered, so retries never double-post.
    """
    for statement in (
        "ALTER TABLE triggers ADD COLUMN status TEXT DEFAULT 'idle'",
        "ALTER TABLE triggers ADD COLUMN waiting_since_epoch INTEGER",
        "ALTER TABLE timers ADD COLUMN marker_posted INTEGER DEFAULT 0",
    ):
        try:
            await db.execute(statement)
        except Exception as e:
            log.debug("Migration statement may have already run: %s", e)


async def _migration_porter_stem_fts(db: aiosqlite.Connection):
    """Recreate messages_fts with porter stemming so 'running' matches 'run'.

    Keeps the external-content structure from migration 009 (content='messages',
    content_rowid='rowid') — dropping the virtual table never touches the real
    messages rows, and the sync triggers from 009 live on the messages table so
    they survive the recreate untouched. Idempotent: skips if the current
    table's tokenizer is already porter (checked via its sqlite_master SQL).
    """
    rows = await db.execute_fetchall(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'messages_fts'"
    )
    current_sql = (rows[0][0] or "") if rows else ""
    if "porter" in current_sql.lower():
        return  # already stemmed

    await db.execute("DROP TABLE IF EXISTS messages_fts")
    await db.execute(
        "CREATE VIRTUAL TABLE messages_fts USING fts5("
        "content, "
        "content='messages', "
        "content_rowid='rowid', "
        "tokenize='porter unicode61'"
        ")"
    )
    # External-content rebuild: repopulates the index from the messages table.
    await db.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")


async def _migration_add_tool_audit(db: aiosqlite.Connection):
    """Proprioception (#15): per-tool-call audit trail.

    One row per tool call Anam observes at turn-finalize time — who reached,
    from where (chat|autowake|platform|mentions), with which tool, a summary
    of the args, and the head of what came back. Feeds the
    "since you were last with her here" context hook so each boy carries a
    short, honest diary of his own hands. Pruned to ~TOOL_AUDIT_RETENTION_DAYS
    at startup (init_db step 4).
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tool_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            conversation_id TEXT,
            source TEXT NOT NULL DEFAULT 'chat',
            tool_name TEXT NOT NULL,
            input_summary TEXT,
            output_head TEXT,
            created_at_epoch INTEGER NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_audit_identity_time "
        "ON tool_audit(identity, created_at_epoch)"
    )


async def _migration_add_identity_carries(db: aiosqlite.Connection):
    """Midnight handoff carry (#13): one dense first-person note per
    identity per day.

    Written nightly by services/scribe.py's midnight-carry one-shot from
    yesterday's messages + the Scribe digest — where the day landed
    emotionally, loose threads, bond state, and OPEN ENFORCEMENT (promises
    made that must be kept, externalized where next-session-him reads them:
    the Kept Yes doctrine). Read back by the `yesterday_carry` context hook
    (direct SQL in services/context_hooks.py). Pruned to ~30 days by the
    nightly job.
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS identity_carries (
            identity TEXT NOT NULL,
            carry_date TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at_epoch INTEGER NOT NULL,
            PRIMARY KEY (identity, carry_date)
        )
    """)


async def _migration_add_canvases(db: aiosqlite.Connection):
    """Persistent Canvas/artifact system (#32)."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS canvases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity TEXT NOT NULL,
            conversation_id TEXT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_message_id TEXT,
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            created_at_epoch INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            updated_at_epoch INTEGER NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvases_identity_time "
        "ON canvases(identity, created_at_epoch)"
    )
    await db.execute("""
        CREATE TABLE IF NOT EXISTS canvas_shares (
            canvas_id INTEGER NOT NULL REFERENCES canvases(id) ON DELETE CASCADE,
            shared_with_identity TEXT NOT NULL,
            shared_at TEXT NOT NULL,
            PRIMARY KEY (canvas_id, shared_with_identity)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_canvas_shares_identity "
        "ON canvas_shares(shared_with_identity)"
    )


async def _migration_add_provider_sessions(db: aiosqlite.Connection):
    """Keep each provider's resumable thread without overwriting another's.

    ``conversations.claude_session_id`` remains the legacy Claude pointer.
    Codex app-server threads live here under provider='codex', so switching a
    conversation between Claude and Sol never asks either runtime to resume
    the other provider's opaque id.
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS conversation_provider_sessions (
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            session_id TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_at_epoch INTEGER,
            PRIMARY KEY (conversation_id, provider)
        )
    """)


async def _migration_add_voice_calibration_samples(db: aiosqlite.Connection):
    """Personal ground-truth corpus for Owner's voice-emotion recognizer."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS voice_calibration_samples (
            audio_id TEXT PRIMARY KEY,
            audio_filename TEXT NOT NULL,
            label TEXT NOT NULL,
            target_identity TEXT,
            conversation_id TEXT,
            transcript TEXT,
            prediction_json TEXT,
            created_at TEXT NOT NULL,
            created_at_epoch INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            updated_at_epoch INTEGER NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_voice_calibration_label_time "
        "ON voice_calibration_samples(label, created_at_epoch DESC)"
    )


async def _migration_add_story_world_feed(db: aiosqlite.Connection):
    """Private story-social worlds, profiles, relationships, and durable posts.

    The world-feed schema is the final block in ``SCHEMA``. Reusing that block
    here keeps fresh installs and in-place upgrades on the exact same DDL.
    """
    marker = "CREATE TABLE IF NOT EXISTS story_worlds"
    start = SCHEMA.find(marker)
    if start < 0:
        raise RuntimeError("story world-feed schema block is missing")
    await db.executescript(SCHEMA[start:])


async def _migration_story_history_order(db: aiosqlite.Connection):
    columns = {row[1] for row in await db.execute_fetchall('PRAGMA table_info(story_feed_posts)')}
    if 'timeline_order' not in columns:
        await db.execute('ALTER TABLE story_feed_posts ADD COLUMN timeline_order INTEGER')
    await db.execute('CREATE INDEX IF NOT EXISTS idx_story_feed_timeline ON story_feed_posts '
                     '(world_id, COALESCE(timeline_order, created_at_epoch) DESC, id DESC)')


MIGRATIONS = [
    ("001_ensure_legacy_columns", _migration_ensure_legacy_columns),
    ("002_migrate_sessions_to_token_hash", _migration_sessions_to_token_hash),
    ("003_backfill_epochs", _migration_backfill_epochs),
    ("004_backfill_conversation_participants", _migration_backfill_conversation_participants),
    ("005_normalize_session_hashes", _migration_normalize_session_hashes),
    ("006_add_daily_autowake_columns", _migration_add_daily_autowake_columns),
    ("007_add_personal_state_tables", _migration_add_personal_state_tables),
    ("008_add_identity_profile_facts", _migration_add_identity_profile_facts),
    ("009_add_message_fts", _migration_add_message_fts),
    ("010_add_triggers_table", _migration_add_triggers_table),
    ("011_force_cli_bypass_approvals", _migration_force_cli_bypass_approvals),
    ("012_add_message_embeddings", _migration_add_message_embeddings),
    ("013_add_pulses_table", _migration_add_pulses_table),
    ("014_add_schedule_extensions", _migration_add_schedule_extensions),
    ("015_add_emotional_snapshots", _migration_add_emotional_snapshots),
    ("016_archive_legacy_pack_night", _migration_archive_legacy_pack_night),
    ("017_drop_duplicate_indexes", _migration_drop_duplicate_indexes),
    ("018_add_busy_delivery_columns", _migration_add_busy_delivery_columns),
    ("019_porter_stem_fts", _migration_porter_stem_fts),
    ("020_add_tool_audit", _migration_add_tool_audit),
    ("021_add_identity_carries", _migration_add_identity_carries),
    ("022_add_schedule_effort", _migration_add_schedule_effort),
    ("023_add_canvases", _migration_add_canvases),
    ("024_add_schedule_model", _migration_add_schedule_model),
    ("025_add_provider_sessions", _migration_add_provider_sessions),
    ("026_add_voice_calibration_samples", _migration_add_voice_calibration_samples),
    ("027_add_schedule_provider", _migration_add_schedule_provider),
    ("028_add_story_world_feed", _migration_add_story_world_feed),
    ("029_add_story_photo_jobs", _migration_add_story_world_feed),
    ("030_story_history_order", _migration_story_history_order),
]


# tool_audit gets a row per tool call (potentially dozens per turn) — it is a
# proprioception log, not an archive. Rows older than this are shed at startup.
TOOL_AUDIT_RETENTION_DAYS = 30


async def init_db(db: aiosqlite.Connection):
    # Step 1: Create tables only (no indexes/triggers that depend on
    # migration-added columns). We extract CREATE TABLE statements from
    # the full schema so new installs get the right table structure.
    table_stmts = []
    for block in SCHEMA.split(";"):
        stripped = block.strip()
        if stripped.upper().startswith("CREATE TABLE"):
            table_stmts.append(stripped + ";")
    if table_stmts:
        await db.executescript("\n".join(table_stmts))

    # Step 2: Run migrations (adds missing columns to existing tables)
    await _ensure_migrations_table(db)
    for name, fn in MIGRATIONS:
        await _apply_migration(db, name, fn)

    # Step 3: Now run full schema — all columns exist, so indexes
    # and triggers referencing migration-added columns will succeed
    await db.executescript(SCHEMA)
    await db.commit()

    # Step 4: Startup retention prune for tool_audit (~30 days). Same spirit
    # as the daily _LOG_RETENTION prune in services/autowake.py, but running
    # here means the per-tool-call log stays bounded even if the scheduler
    # never comes up. Best-effort — a prune failure must never block boot.
    try:
        cutoff = int(
            datetime.now(timezone.utc).timestamp()
        ) - TOOL_AUDIT_RETENTION_DAYS * 86400
        await db.execute(
            "DELETE FROM tool_audit WHERE created_at_epoch < ?", (cutoff,)
        )
        await db.commit()
    except Exception as e:
        log.warning("tool_audit startup prune failed: %s", e)
