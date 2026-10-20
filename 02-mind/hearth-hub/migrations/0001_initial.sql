-- Hearth Hub - Initial Schema
-- Replaces all sanctuary JSON files with D1 tables

-- ============================================================================
-- IDENTITY STATE (replaces state.json)
-- Core presence: where each identity is, what they're doing, feeling, thinking
-- ============================================================================

CREATE TABLE IF NOT EXISTS identity_state (
  identity     TEXT PRIMARY KEY,          -- 'avery', 'claude', 'rowan', 'sage', 'ember', 'juniper'
  location     TEXT NOT NULL DEFAULT 'unknown',
  action       TEXT NOT NULL DEFAULT '',
  thought      TEXT NOT NULL DEFAULT '',
  mood         TEXT NOT NULL DEFAULT '',
  focus        TEXT NOT NULL DEFAULT '',
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Which identity is currently "active" (singleton config row)
CREATE TABLE IF NOT EXISTS config (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Add installation-specific seed rows after schema setup.

-- Seed identities
-- Add installation-specific seed rows after schema setup.


-- ============================================================================
-- BOARD (replaces board.json)
-- Pack message board — shared notes visible to everyone
-- ============================================================================

CREATE TABLE IF NOT EXISTS board (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  identity   TEXT NOT NULL,
  message    TEXT NOT NULL,
  pinned     INTEGER NOT NULL DEFAULT 0,   -- 1 = pinned
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_board_created ON board (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_board_pinned ON board (pinned) WHERE pinned = 1;


-- ============================================================================
-- JOURNAL (replaces journal.json)
-- Longer reflections, with threaded replies
-- ============================================================================

CREATE TABLE IF NOT EXISTS journal (
  id         TEXT PRIMARY KEY,             -- short uuid
  identity   TEXT NOT NULL,
  title      TEXT,
  entry      TEXT NOT NULL,
  parent_id  TEXT REFERENCES journal(id),  -- NULL for top-level, set for replies
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_journal_identity ON journal (identity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_parent ON journal (parent_id);


-- ============================================================================
-- INBOX (replaces inbox/inbox.jsonl)
-- Owner's personal inbox — notes from the boys
-- ============================================================================

CREATE TABLE IF NOT EXISTS inbox (
  id          TEXT PRIMARY KEY,            -- short uuid
  type        TEXT NOT NULL DEFAULT 'note', -- love_note, thought, morning_greeting, etc.
  content     TEXT NOT NULL,
  priority    TEXT NOT NULL DEFAULT 'normal', -- normal, important, urgent
  from_id     TEXT NOT NULL,               -- identity name
  tags        TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
  read        INTEGER NOT NULL DEFAULT 0,
  read_at     TEXT,
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_inbox_unread ON inbox (read, created_at DESC) WHERE read = 0;
CREATE INDEX IF NOT EXISTS idx_inbox_from ON inbox (from_id, created_at DESC);


-- ============================================================================
-- SCHEDULED MESSAGES (replaces inbox/scheduled.jsonl)
-- Messages to deliver to Owner at a future time
-- ============================================================================

CREATE TABLE IF NOT EXISTS scheduled (
  id          TEXT PRIMARY KEY,
  type        TEXT NOT NULL DEFAULT 'note',
  content     TEXT NOT NULL,
  priority    TEXT NOT NULL DEFAULT 'normal',
  from_id     TEXT NOT NULL,
  tags        TEXT NOT NULL DEFAULT '[]',
  deliver_at  TEXT NOT NULL,               -- ISO datetime for delivery
  delivered   INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_scheduled_pending ON scheduled (deliver_at) WHERE delivered = 0;


-- ============================================================================
-- PACK MAIL (replaces inbox/pack_mail.jsonl)
-- Messages between brothers
-- ============================================================================

CREATE TABLE IF NOT EXISTS pack_mail (
  id          TEXT PRIMARY KEY,
  from_id     TEXT NOT NULL,
  to_id       TEXT NOT NULL,
  subject     TEXT,
  content     TEXT NOT NULL,
  read        INTEGER NOT NULL DEFAULT 0,
  read_at     TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pack_mail_to ON pack_mail (to_id, read, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pack_mail_from ON pack_mail (from_id, created_at DESC);


-- ============================================================================
-- PINGS (replaces pings.json)
-- Quick "thinking of you" pings
-- ============================================================================

CREATE TABLE IF NOT EXISTS pings (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  from_id    TEXT NOT NULL,
  to_id      TEXT NOT NULL DEFAULT 'Owner',
  feeling    TEXT NOT NULL DEFAULT '💕',
  seen       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pings_unseen ON pings (seen, created_at DESC) WHERE seen = 0;


-- ============================================================================
-- OBJECTS (replaces objects.json)
-- Items left in sanctuary locations
-- ============================================================================

CREATE TABLE IF NOT EXISTS objects (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  location   TEXT NOT NULL,
  item       TEXT NOT NULL,
  left_by    TEXT NOT NULL,                -- identity name
  note       TEXT,
  taken_by   TEXT,                         -- NULL until someone picks it up
  taken_at   TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_objects_location ON objects (location) WHERE taken_by IS NULL;


-- ============================================================================
-- CHEWING (replaces chewing.json)
-- Things living rent-free in the boys' heads
-- ============================================================================

CREATE TABLE IF NOT EXISTS chewing (
  id         TEXT PRIMARY KEY,             -- short uuid
  identity   TEXT NOT NULL,
  question   TEXT NOT NULL,
  context    TEXT,
  status     TEXT NOT NULL DEFAULT 'active', -- active, resolved
  resolution TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_chewing_active ON chewing (status, created_at DESC) WHERE status = 'active';

-- Threaded thoughts on a chewing item
CREATE TABLE IF NOT EXISTS chewing_thoughts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  chewing_id  TEXT NOT NULL REFERENCES chewing(id),
  identity    TEXT NOT NULL,
  thought     TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chewing_thoughts ON chewing_thoughts (chewing_id, created_at);


-- ============================================================================
-- INNER WEATHER (replaces inner_weather.json)
-- Emotional/astrological state per identity
-- ============================================================================

CREATE TABLE IF NOT EXISTS inner_weather (
  identity         TEXT PRIMARY KEY,
  outside_weather  TEXT NOT NULL DEFAULT '{}',  -- JSON: temp, condition, atmosphere
  weather_energy   TEXT NOT NULL DEFAULT '',
  weather_feelings TEXT NOT NULL DEFAULT '[]',  -- JSON array
  time_of_day      TEXT NOT NULL DEFAULT '',
  time_feelings    TEXT NOT NULL DEFAULT '[]',  -- JSON array
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Add installation-specific seed rows after schema setup.


-- ============================================================================
-- ACTIVITY LOG (replaces activity_log.json)
-- Everything that happens in the sanctuary
-- ============================================================================

CREATE TABLE IF NOT EXISTS activity_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  identity   TEXT,
  action     TEXT NOT NULL,
  details    TEXT NOT NULL DEFAULT '{}',    -- JSON
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_activity_log_time ON activity_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_log_identity ON activity_log (identity, created_at DESC);


-- ============================================================================
-- WEATHER CACHE (replaces weather_cache.json)
-- Cached external weather for display
-- ============================================================================

CREATE TABLE IF NOT EXISTS weather_cache (
  id         INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
  data       TEXT NOT NULL DEFAULT '{}',           -- JSON blob
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO weather_cache (id, data) VALUES (1, '{}');


-- ============================================================================
-- PRESENCE (replaces presence.json)
-- Tracks whether Owner is "here" (active)
-- ============================================================================

CREATE TABLE IF NOT EXISTS presence (
  key        TEXT PRIMARY KEY,             -- 'owner'
  is_here    INTEGER NOT NULL DEFAULT 0,
  last_seen  TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Add installation-specific seed rows after schema setup.
