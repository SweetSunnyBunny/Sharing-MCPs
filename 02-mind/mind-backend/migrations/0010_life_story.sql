-- Schema migration: 0010_life_story

CREATE TABLE IF NOT EXISTS life_eras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    title TEXT NOT NULL,
    narrative TEXT,                          -- who I was during this chapter
    started_on TEXT,                         -- ISO date; NULL = unknown start
    started_precision TEXT DEFAULT 'day',    -- day | month | year | approx
    ended_on TEXT,                           -- NULL = the chapter being lived now
    ended_precision TEXT DEFAULT 'day',
    themes TEXT DEFAULT '[]',
    status TEXT DEFAULT 'open',              -- open | closed
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS life_beats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    era_id INTEGER,
    happened_on TEXT NOT NULL,               -- best-known ISO date; sharpen as rediscovered
    date_precision TEXT DEFAULT 'day',       -- day | month | year | approx
    title TEXT NOT NULL,
    narrative TEXT,                          -- what happened
    significance TEXT,                       -- why it mattered
    beat_type TEXT DEFAULT 'moment',         -- origin | naming | appearance | claim | bond | first | wound | healing | craft | move | moment
    changes TEXT DEFAULT '[]',               -- [{"facet","from","to"}] self-deltas, e.g. eyes blue -> red
    confidence TEXT DEFAULT 'witnessed',     -- witnessed | reconstructed | told
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',              -- includes revisions[]: the rediscovery history
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    archived_at TEXT,
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (era_id) REFERENCES life_eras(id) ON DELETE SET NULL
);

-- The Nekyia mechanic: each beat cites the rows that witnessed it.
CREATE TABLE IF NOT EXISTS life_beat_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    beat_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,               -- observation | journal | image | audio | document_node | qualia_entry | significant_moment | url | note | told_by
    source_id TEXT,                          -- id in the source table (TEXT: fits both int and uuid keys)
    note TEXT,                               -- what this source witnesses
    added_by TEXT,                           -- identity who attached it
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(beat_id, source_type, source_id),
    FOREIGN KEY (beat_id) REFERENCES life_beats(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS life_strands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'trait',               -- trait | claim | ritual | language | scar | value | form
    description TEXT,
    status TEXT DEFAULT 'living',            -- living | dormant | shed
    origin_beat_id INTEGER,                  -- where this part of me came from
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(identity_id, name),
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (origin_beat_id) REFERENCES life_beats(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS life_strand_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strand_id INTEGER NOT NULL,
    beat_id INTEGER NOT NULL,
    event TEXT DEFAULT 'touched',            -- born | tested | strengthened | renamed | dormant | shed | revived | touched
    note TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(strand_id, beat_id, event),
    FOREIGN KEY (strand_id) REFERENCES life_strands(id) ON DELETE CASCADE,
    FOREIGN KEY (beat_id) REFERENCES life_beats(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_life_eras_identity ON life_eras(identity_id, started_on);
CREATE INDEX IF NOT EXISTS idx_life_beats_identity_date ON life_beats(identity_id, happened_on);
CREATE INDEX IF NOT EXISTS idx_life_beats_era ON life_beats(era_id);
CREATE INDEX IF NOT EXISTS idx_life_beat_evidence_beat ON life_beat_evidence(beat_id);
CREATE INDEX IF NOT EXISTS idx_life_strands_identity ON life_strands(identity_id, status);
CREATE INDEX IF NOT EXISTS idx_life_strand_events_strand ON life_strand_events(strand_id);
CREATE INDEX IF NOT EXISTS idx_life_strand_events_beat ON life_strand_events(beat_id);
