-- KV store for cross-device JSON object syncing.
-- Uses UUID as the primary key (client-supplied).
-- The secret column stores a SHA-256 hex hash of the user's plaintext secret.

CREATE TABLE IF NOT EXISTS sync_kv (
    id         UUID PRIMARY KEY,
    secret     TEXT NOT NULL,
    value      JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
