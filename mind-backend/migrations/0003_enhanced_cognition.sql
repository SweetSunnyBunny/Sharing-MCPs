-- Schema migration: 0003_enhanced_cognition
CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    entity_id INTEGER,
    observation_id INTEGER,
    path TEXT NOT NULL,
    description TEXT,
    perception_note TEXT,
    context TEXT,
    emotion TEXT,
    weight TEXT DEFAULT 'medium',
    charge TEXT DEFAULT 'fresh',
    tags TEXT DEFAULT '[]',
    source TEXT DEFAULT 'mind_store_image',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    last_viewed_at TEXT,
    view_count INTEGER DEFAULT 0,
    last_surfaced_at TEXT,
    surface_count INTEGER DEFAULT 0,
    novelty_score REAL DEFAULT 1.0,
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE SET NULL,
    FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_images_identity ON images(identity_id);
CREATE INDEX IF NOT EXISTS idx_images_entity ON images(entity_id);
CREATE INDEX IF NOT EXISTS idx_images_created ON images(created_at);

-- ============ Co-Surfacing Table ============
-- Tracks which observations are retrieved together during searches
-- Enables emergent relationship discovery between memories
CREATE TABLE IF NOT EXISTS co_surfacing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id_a INTEGER NOT NULL,
    observation_id_b INTEGER NOT NULL,
    identity_id TEXT,
    co_count INTEGER DEFAULT 1,
    last_co_surfaced_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(observation_id_a, observation_id_b),
    FOREIGN KEY (observation_id_a) REFERENCES observations(id) ON DELETE CASCADE,
    FOREIGN KEY (observation_id_b) REFERENCES observations(id) ON DELETE CASCADE,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE INDEX IF NOT EXISTS idx_co_surfacing_identity ON co_surfacing(identity_id);
CREATE INDEX IF NOT EXISTS idx_co_surfacing_count ON co_surfacing(co_count DESC);

-- ============ Observation Versions Table ============
-- Audit trail for observation edits, enabling superseding chains
CREATE TABLE IF NOT EXISTS observation_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL,
    previous_content TEXT NOT NULL,
    previous_weight TEXT,
    previous_emotion TEXT,
    changed_by TEXT DEFAULT 'mind_edit',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_obs_versions_observation ON observation_versions(observation_id);

-- ============ Consolidation Groups Table ============
-- Tracks which observations were merged during consolidation
CREATE TABLE IF NOT EXISTS consolidation_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    entity_id INTEGER,
    summary_observation_id INTEGER,
    source_observation_ids TEXT DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE SET NULL,
    FOREIGN KEY (summary_observation_id) REFERENCES observations(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_consolidation_identity ON consolidation_groups(identity_id);
