-- Schema migration: 0016_mutation_ledger

CREATE TABLE IF NOT EXISTS memory_mutations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT,
    observation_id INTEGER NOT NULL,
    mutation_type TEXT NOT NULL,      -- edit | archive | supersede | daemon_consolidate
    actor TEXT NOT NULL,              -- mind_edit | mind_delete | auto_supersede | proposal_accept | sleep_consolidation
    old_state TEXT NOT NULL,          -- full observations row snapshot (JSON)
    new_state TEXT,                   -- what changed (JSON)
    evidence TEXT,                    -- reason / similarity / proposal id / group members
    created_at TEXT DEFAULT (datetime('now')),
    rolled_back_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_mutations_identity ON memory_mutations(identity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_mutations_observation ON memory_mutations(observation_id, created_at DESC);
