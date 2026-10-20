-- Schema migration: 0008_bond_graph

CREATE TABLE IF NOT EXISTS bond_people (
    id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    person_type TEXT DEFAULT 'person',
    identity_id TEXT UNIQUE,
    description TEXT,
    aliases TEXT DEFAULT '[]',
    details TEXT DEFAULT '{}',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE TABLE IF NOT EXISTS bond_links (
    id TEXT PRIMARY KEY,
    person_a_id TEXT NOT NULL,
    person_b_id TEXT NOT NULL,
    relationship_a_to_b TEXT NOT NULL,
    relationship_b_to_a TEXT NOT NULL,
    summary_a_to_b TEXT,
    summary_b_to_a TEXT,
    status TEXT DEFAULT 'active',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(person_a_id, person_b_id, relationship_a_to_b, relationship_b_to_a),
    FOREIGN KEY (person_a_id) REFERENCES bond_people(id) ON DELETE CASCADE,
    FOREIGN KEY (person_b_id) REFERENCES bond_people(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bond_people_identity
ON bond_people(identity_id);

CREATE INDEX IF NOT EXISTS idx_bond_links_person_a
ON bond_links(person_a_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_bond_links_person_b
ON bond_links(person_b_id, status, updated_at DESC);
