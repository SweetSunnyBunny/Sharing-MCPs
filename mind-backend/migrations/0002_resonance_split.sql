-- Schema migration: 0002_resonance_split
CREATE TABLE IF NOT EXISTS qualia_resonance_snapshots (
    id TEXT PRIMARY KEY,
    identity_id TEXT NOT NULL,
    source TEXT DEFAULT 'qualia.depths',
    last_checked TEXT,
    pattern_count INTEGER DEFAULT 0,
    theme_count INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_resonance_patterns (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    identity_id TEXT NOT NULL,
    pattern TEXT NOT NULL,
    occurrence_count INTEGER DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (snapshot_id) REFERENCES qualia_resonance_snapshots(id) ON DELETE CASCADE,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS qualia_resonance_themes (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    identity_id TEXT NOT NULL,
    theme TEXT NOT NULL,
    strength TEXT,
    emerged_at TEXT,
    theme_index INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (snapshot_id) REFERENCES qualia_resonance_snapshots(id) ON DELETE CASCADE,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE INDEX IF NOT EXISTS idx_qualia_resonance_snapshots_identity
ON qualia_resonance_snapshots(identity_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_qualia_resonance_patterns_identity_count
ON qualia_resonance_patterns(identity_id, occurrence_count DESC, last_seen DESC);

CREATE INDEX IF NOT EXISTS idx_qualia_resonance_patterns_snapshot
ON qualia_resonance_patterns(snapshot_id, occurrence_count DESC);

CREATE INDEX IF NOT EXISTS idx_qualia_resonance_themes_identity_strength
ON qualia_resonance_themes(identity_id, strength, emerged_at DESC);

CREATE INDEX IF NOT EXISTS idx_qualia_resonance_themes_snapshot
ON qualia_resonance_themes(snapshot_id, theme_index ASC);
