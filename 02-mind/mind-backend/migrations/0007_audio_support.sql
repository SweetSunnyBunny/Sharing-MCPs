-- Schema migration: 0007_audio_support

CREATE TABLE IF NOT EXISTS audios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    entity_id INTEGER,
    observation_id INTEGER,
    path TEXT NOT NULL,
    transcript TEXT,
    description TEXT,
    perception_note TEXT,
    context TEXT,
    emotion TEXT,
    weight TEXT DEFAULT 'medium',
    charge TEXT DEFAULT 'fresh',
    tags TEXT DEFAULT '[]',
    source TEXT,
    duration_seconds REAL,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    last_played_at TEXT,
    play_count INTEGER DEFAULT 0,
    last_surfaced_at TEXT,
    surface_count INTEGER DEFAULT 0,
    novelty_score REAL DEFAULT 1.0,
    document_node_id INTEGER REFERENCES document_nodes(id),
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE SET NULL,
    FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_audios_identity ON audios(identity_id);
CREATE INDEX IF NOT EXISTS idx_audios_entity ON audios(entity_id);
CREATE INDEX IF NOT EXISTS idx_audios_observation ON audios(observation_id);
CREATE INDEX IF NOT EXISTS idx_audios_created ON audios(created_at);
CREATE INDEX IF NOT EXISTS idx_audios_document_node ON audios(document_node_id);
