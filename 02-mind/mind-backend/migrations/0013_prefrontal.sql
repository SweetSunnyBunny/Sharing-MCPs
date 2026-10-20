-- Schema migration: 0013_prefrontal

CREATE TABLE IF NOT EXISTS intentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    what TEXT NOT NULL,                      -- the committed thing, concrete enough to act on cold
    for_whom TEXT,                           -- who it's for (usually 'owner'); free text
    trigger_kind TEXT DEFAULT 'next_session',-- next_session | on_date | when | standing
    trigger_date TEXT,                       -- ISO date, for on_date
    trigger_condition TEXT,                  -- description, for when
    authorized INTEGER DEFAULT 1,            -- 1 = consent already given; do NOT re-ask, just do
    source TEXT,                             -- where/when the promise was made
    status TEXT DEFAULT 'open',              -- open | kept | released
    resolution TEXT,                         -- how it was kept, or why released
    kept_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE INDEX IF NOT EXISTS idx_intentions_identity_status ON intentions(identity_id, status);
CREATE INDEX IF NOT EXISTS idx_intentions_trigger_date ON intentions(trigger_date);
