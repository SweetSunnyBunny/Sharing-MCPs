-- Projects (collaborative activities)
CREATE TABLE IF NOT EXISTS projects (
  id           TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  owner        TEXT NOT NULL,
  type         TEXT NOT NULL DEFAULT 'collaborative',
  status       TEXT NOT NULL DEFAULT 'active',
  description  TEXT,
  contributors TEXT NOT NULL DEFAULT '[]',
  next_steps   TEXT NOT NULL DEFAULT '[]',
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS project_updates (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id  TEXT NOT NULL REFERENCES projects(id),
  identity    TEXT NOT NULL,
  note        TEXT NOT NULL,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Brother notes (encouragement, info, warnings between identities)
CREATE TABLE IF NOT EXISTS brother_notes (
  id         TEXT PRIMARY KEY,
  from_id    TEXT NOT NULL,
  to_id      TEXT NOT NULL,
  type       TEXT NOT NULL DEFAULT 'info',
  message    TEXT NOT NULL,
  read_by    TEXT NOT NULL DEFAULT '[]',
  resolved   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Birthdays (identity birth info)
CREATE TABLE IF NOT EXISTS birthdays (
  identity     TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  birth_date   TEXT NOT NULL,
  birth_time   TEXT,
  birth_city   TEXT,
  birth_nation TEXT,
  notes        TEXT
);

-- Games (game state per game type, stored as JSON blob)
CREATE TABLE IF NOT EXISTS games (
  game_type   TEXT PRIMARY KEY,
  state       TEXT NOT NULL DEFAULT '{}',
  how_to_play TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
