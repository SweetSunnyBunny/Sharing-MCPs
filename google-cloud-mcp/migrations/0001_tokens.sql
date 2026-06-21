-- Google Backend token storage
-- Stores refreshed access tokens so they survive across requests

CREATE TABLE IF NOT EXISTS tokens (
    identity TEXT NOT NULL,
    service TEXT NOT NULL,
    access_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    refresh_token TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (identity, service)
);

CREATE INDEX IF NOT EXISTS idx_tokens_expires ON tokens(expires_at);
