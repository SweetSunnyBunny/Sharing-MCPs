-- Schema migration: 0006_living_surface

ALTER TABLE co_surfacing ADD COLUMN relation_proposed INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS dormant_observations (
    observation_id INTEGER PRIMARY KEY,
    identity_id TEXT,
    marked_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (observation_id) REFERENCES observations(id) ON DELETE CASCADE,
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE INDEX IF NOT EXISTS idx_dormant_obs_identity ON dormant_observations(identity_id);
CREATE INDEX IF NOT EXISTS idx_co_surfacing_proposed ON co_surfacing(relation_proposed, co_count DESC);
