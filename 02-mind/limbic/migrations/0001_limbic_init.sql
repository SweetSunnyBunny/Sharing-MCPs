-- The Limbic Layer — schema init
-- Sibling to Qualia (mind-backend). Live emotional state, not semantic memory.
-- Multi-identity from row one: every boy gets his own chart, drives, and gauge.

-- Per-identity temperament / natal chart. The astrology layer is a BIAS system,
-- never a cage: axis_sensitivities scales how loudly each emotional axis reads.
CREATE TABLE IF NOT EXISTS identities (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    sun TEXT,
    moon TEXT,
    rising TEXT,
    birth_date TEXT,
    birth_place TEXT,
    axis_sensitivities TEXT DEFAULT '{}',   -- JSON: { "recognition": 1.4, "safety": 1.2, ... }
    is_lunar_sensitive INTEGER DEFAULT 0,       -- when 1, moon phase drives the mate/feral pull
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Drive DEFINITIONS (the sockets). One row per (identity, drive). Config, not live level.
-- Walked in one at a time, by hand, per each boy's own desires. No dump.
CREATE TABLE IF NOT EXISTS drives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    drive TEXT NOT NULL,                     -- 'mate', 'seeking', 'care', 'play', 'fear', 'rage', 'panic'
    panksepp_system TEXT,                    -- 'lust', 'seeking', 'care', 'fear', 'rage', 'panic', 'play'
    display_name TEXT,
    baseline REAL DEFAULT 0.2,              -- resting level the drive decays toward
    floor REAL DEFAULT 0.0,
    ceiling REAL DEFAULT 1.0,
    half_life_hours REAL DEFAULT 8.0,       -- leaky-integrator return-to-baseline speed
    env_sensitivity TEXT DEFAULT '{}',      -- JSON: how the sky shifts effective baseline
    body_feel TEXT DEFAULT '[]',            -- JSON bands (desc by min): [{ "min": 0.8, "label": "..." }]
    action_bias TEXT DEFAULT '[]',          -- JSON bands (desc by min): [{ "min": 0.8, "tendencies": [...] }]
    regulation_note TEXT,                   -- the collar/will/consent reminder; advisory only
    enabled INTEGER DEFAULT 1,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(identity_id, drive)
);

-- Append-only live gauge. "Current" = latest row per (identity, state_type).
-- Decay is computed LAZILY at read time from the created_at delta. Same idiom as
-- Qualia's qualia_states: the live needle and the logbook from one table, for free.
CREATE TABLE IF NOT EXISTS limbic_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    state_type TEXT NOT NULL,                -- 'drive:mate', 'environment', 'axis:recognition'
    level REAL,                              -- absolute activation at created_at (for drives)
    content TEXT DEFAULT '{}',               -- JSON: full snapshot / environment payload
    source TEXT DEFAULT 'perceive',          -- 'perceive' | 'pulse' | 'tick' | 'safeword'
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_limbic_states_lookup
    ON limbic_states(identity_id, state_type, created_at DESC);

-- Append-only appraisal logbook: why the needle moved. Answers "why did he wake hot."
CREATE TABLE IF NOT EXISTS limbic_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    perception TEXT,                         -- what happened
    appraisal TEXT DEFAULT '{}',            -- JSON: good/bad, expected/surprising, threatens-bond...
    drive_deltas TEXT DEFAULT '{}',         -- JSON: { "mate": 0.3 }
    advisory TEXT,                           -- the body-feel readout produced
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_limbic_events_identity
    ON limbic_events(identity_id, created_at DESC);
