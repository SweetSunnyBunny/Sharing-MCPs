CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe TEXT NOT NULL UNIQUE,             -- slug: 'afterglow'
    display_name TEXT,
    feel TEXT,                               -- what the state is, in prose
    conditions TEXT DEFAULT '{}',            -- JSON signature (shape above)
    tints TEXT DEFAULT '{}',                 -- JSON per-identity tells/triggers
    blend_note TEXT,
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
