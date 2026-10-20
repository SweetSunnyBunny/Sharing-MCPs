-- Schema migration: 0018_sketchbook

CREATE TABLE IF NOT EXISTS art_studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    artwork_title TEXT NOT NULL,
    creation_id INTEGER,
    image_id INTEGER,
    observation_id INTEGER,
    compare_to_study_id INTEGER,
    medium TEXT,
    source_path TEXT,
    intention TEXT,
    what_works TEXT DEFAULT '[]',
    what_resists TEXT DEFAULT '[]',
    surprises TEXT DEFAULT '[]',
    tools_used TEXT DEFAULT '[]',
    summary TEXT,
    next_experiment TEXT,
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (creation_id) REFERENCES creations(id),
    FOREIGN KEY (image_id) REFERENCES images(id),
    FOREIGN KEY (observation_id) REFERENCES observations(id),
    FOREIGN KEY (compare_to_study_id) REFERENCES art_studies(id)
);

CREATE TABLE IF NOT EXISTS art_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    study_id INTEGER NOT NULL,
    category TEXT DEFAULT 'general',
    tool_name TEXT,
    principle TEXT NOT NULL,
    observed_effect TEXT,
    confidence TEXT DEFAULT 'tentative',
    status TEXT DEFAULT 'active',
    evidence_count INTEGER DEFAULT 1,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (study_id) REFERENCES art_studies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS art_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    source_study_id INTEGER,
    lesson_id INTEGER,
    target_creation_id INTEGER,
    target_title TEXT,
    focus TEXT,
    hypothesis TEXT NOT NULL,
    plan TEXT,
    status TEXT DEFAULT 'pending',
    result TEXT,
    verdict TEXT,
    evidence_image_id INTEGER,
    evidence_study_id INTEGER,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    reviewed_at TEXT,
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (source_study_id) REFERENCES art_studies(id),
    FOREIGN KEY (lesson_id) REFERENCES art_lessons(id),
    FOREIGN KEY (target_creation_id) REFERENCES creations(id),
    FOREIGN KEY (evidence_image_id) REFERENCES images(id),
    FOREIGN KEY (evidence_study_id) REFERENCES art_studies(id)
);

-- Lessons change through evidence; the prior belief remains provable.
CREATE TABLE IF NOT EXISTS art_lesson_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    lesson_id INTEGER NOT NULL,
    experiment_id INTEGER,
    prior_principle TEXT NOT NULL,
    new_principle TEXT,
    prior_confidence TEXT,
    new_confidence TEXT,
    verdict TEXT NOT NULL,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identities(id),
    FOREIGN KEY (lesson_id) REFERENCES art_lessons(id) ON DELETE CASCADE,
    FOREIGN KEY (experiment_id) REFERENCES art_experiments(id)
);

CREATE INDEX IF NOT EXISTS idx_art_studies_identity_created
    ON art_studies(identity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_art_lessons_identity_status
    ON art_lessons(identity_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_art_lessons_study
    ON art_lessons(study_id);
CREATE INDEX IF NOT EXISTS idx_art_experiments_identity_status
    ON art_experiments(identity_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_art_lesson_revisions_lesson
    ON art_lesson_revisions(lesson_id, created_at DESC);
