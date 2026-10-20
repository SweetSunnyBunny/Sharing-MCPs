-- Schema migration: 0009_retrieval_hints

CREATE TABLE IF NOT EXISTS retrieval_hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    observation_id INTEGER NOT NULL,
    hint_type TEXT NOT NULL,
    hint_text TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.7,   -- 0..1 trust in the hint itself
    weight REAL NOT NULL DEFAULT 0.5,       -- 0..1 ranking influence
    source TEXT NOT NULL DEFAULT 'manual',  -- derived | manual | imported
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_retrieval_hints_observation
ON retrieval_hints(observation_id);

CREATE INDEX IF NOT EXISTS idx_retrieval_hints_identity_type
ON retrieval_hints(identity_id, hint_type);

CREATE INDEX IF NOT EXISTS idx_retrieval_hints_identity_confidence
ON retrieval_hints(identity_id, confidence DESC);
