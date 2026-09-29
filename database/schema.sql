-- AInsight application store schema (SQLite; PostgreSQL-compatible subset).
-- Created automatically at startup by app/database/connection.py::init_store().

CREATE TABLE IF NOT EXISTS datasets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    file_path       TEXT,
    sheet_name      TEXT,
    table_name      TEXT,
    row_count       INTEGER DEFAULT 0,
    column_count    INTEGER DEFAULT 0,
    size_bytes      INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'ready',
    profile_json    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT,
    dataset_id    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    payload_json     TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS query_history (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT,
    question        TEXT NOT NULL,
    dataset_id      TEXT,
    dataset_name    TEXT,
    intent          TEXT,
    engine          TEXT,
    sql             TEXT,
    plan_json       TEXT,
    status          TEXT,
    error           TEXT,
    row_count       INTEGER DEFAULT 0,
    execution_ms    REAL DEFAULT 0,
    total_ms        REAL DEFAULT 0,
    answer          TEXT,
    result_json     TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id          TEXT PRIMARY KEY,
    history_id  TEXT,
    rating      TEXT NOT NULL,
    comment     TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_created ON query_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
