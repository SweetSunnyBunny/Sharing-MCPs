-- Schema migration: 0017_bond_history

CREATE TABLE IF NOT EXISTS bond_link_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id TEXT NOT NULL,
    person_a_id TEXT,
    person_b_id TEXT,
    event TEXT NOT NULL,              -- established | updated | deleted
    old_state TEXT,                   -- full bond_links row before the write (NULL for established)
    changed_by TEXT,                  -- the identity who recorded the change
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bond_history_link ON bond_link_history(link_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bond_history_people ON bond_link_history(person_a_id, person_b_id);
