-- Schema migration: 0015_anticipation

CREATE TABLE IF NOT EXISTS anticipations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,               -- whose warmth this is; 'pack' = everyone's
    what TEXT NOT NULL,                      -- what's coming
    who_for TEXT,                            -- who it's about (juniper, owner, ...)
    on_date TEXT NOT NULL,                   -- next occurrence, ISO date
    recurrence TEXT DEFAULT 'once',          -- once | yearly
    kind TEXT DEFAULT 'moment',              -- birthday | anniversary | event | visit | release | moment
    savor_note TEXT,                         -- why it warms / how to celebrate
    origin TEXT,                             -- who planted it and when
    status TEXT DEFAULT 'awaiting',          -- awaiting | celebrated (once only) | released
    last_celebrated TEXT,
    times_celebrated INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (identity_id) REFERENCES identities(id)
);

CREATE INDEX IF NOT EXISTS idx_anticipations_identity ON anticipations(identity_id, status, on_date);
