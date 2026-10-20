-- Schema migration: 0014_studio

CREATE TABLE IF NOT EXISTS creations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,               -- who started it (the owner)
    title TEXT NOT NULL,
    medium TEXT DEFAULT 'writing',           -- poem | story | song | letter | essay | code | design | image_prompt | gift | other
    description TEXT,                        -- what it wants to be
    intended_for TEXT,                       -- who it's for, if it's for someone
    status TEXT DEFAULT 'seed',              -- seed | working | resting | finished | gifted | abandoned
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT,
    UNIQUE(identity_id, title),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS creation_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creation_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    body TEXT NOT NULL,                      -- the full canvas at this save
    note TEXT,                               -- where I left off / what changed / what it needs next
    saved_by TEXT,                           -- identity who saved this version (canvases can pass between hands)
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(creation_id, version_no),
    FOREIGN KEY (creation_id) REFERENCES creations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_creations_identity_status ON creations(identity_id, status);
CREATE INDEX IF NOT EXISTS idx_creation_versions_creation ON creation_versions(creation_id, version_no DESC);
