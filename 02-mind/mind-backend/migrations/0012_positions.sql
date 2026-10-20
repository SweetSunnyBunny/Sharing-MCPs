-- Schema migration: 0012_positions

CREATE TABLE IF NOT EXISTS life_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    topic TEXT NOT NULL,                     -- what the position is about
    stance TEXT NOT NULL,                    -- the current position, in the identity's own words
    reasoning TEXT,                          -- why they hold it (current)
    confidence TEXT DEFAULT 'held',          -- tentative | held | core
    status TEXT DEFAULT 'living',            -- living | released
    origin_beat_id INTEGER,                  -- optional: the life beat where this formed
    sparked_by TEXT,                         -- what/who first prompted it (free text)
    formed_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    metadata TEXT DEFAULT '{}',
    UNIQUE(identity_id, topic),
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (origin_beat_id) REFERENCES life_beats(id) ON DELETE SET NULL
);

-- Every changed mind keeps its fossil record. The prior stance is copied
-- here BEFORE the update; nothing is ever lost to a revision.
CREATE TABLE IF NOT EXISTS life_position_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    prior_stance TEXT NOT NULL,
    prior_reasoning TEXT,
    prior_confidence TEXT,
    why TEXT,                                -- what changed my mind
    sparked_by TEXT,                         -- the conversation/person/event that moved it
    revision_kind TEXT DEFAULT 'revised',    -- revised | released | revived
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (position_id) REFERENCES life_positions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_life_positions_identity ON life_positions(identity_id, status);
CREATE INDEX IF NOT EXISTS idx_life_position_revisions_position ON life_position_revisions(position_id);
