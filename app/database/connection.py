"""Database connections.

Two distinct concerns live here:

1. `app_store()` — the application's own SQLite store (datasets registry, query
   history, conversations, feedback). Read/write, owned by AInsight.
2. `analytics_connection()` — a **read-only** connection used by the SQL agent
   to run generated analytical SQL. It layers three defences: a read-only URI,
   a SQLite authorizer that rejects any non-SELECT operation, and a statement
   limit.

The `DataSourceBackend` indirection keeps the code PostgreSQL-ready: swapping
in psycopg only requires a second backend implementation of the same methods.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config import Config
from app.utils.exceptions import SQLSecurityError
from app.utils.logger import get_logger

log = get_logger("database.connection")
_LOCK = threading.Lock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS datasets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL,
    source_type     TEXT NOT NULL,          -- csv | excel | sqlite
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
"""


def _sqlite_path() -> Path:
    path = Config.sqlite_path(Config.DATABASE_URL)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def app_store() -> Iterator[sqlite3.Connection]:
    """Read/write connection to the application store."""
    if not Config.is_sqlite(Config.DATABASE_URL):
        raise NotImplementedError(
            "Only SQLite is wired up in this build; the store layer is "
            "abstracted so a PostgreSQL backend can be added without touching callers."
        )
    conn = sqlite3.connect(_sqlite_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_store() -> None:
    """Create the application schema if it does not exist yet."""
    with _LOCK, app_store() as conn:
        conn.executescript(SCHEMA_SQL)
    log.info("Application store ready at %s", _sqlite_path())


# --------------------------------------------------------------------------
# Read-only analytical connection
# --------------------------------------------------------------------------

_WRITE_ACTIONS = {
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_PRAGMA,
}


# Read-only pragmas AInsight itself needs to describe a schema. They expose no
# data and cannot modify anything, and they are never reachable from generated
# SQL: the introspection connection is opened only by schema_inspector.
_INTROSPECTION_PRAGMAS = {"table_info", "table_xinfo", "foreign_key_list", "index_list", "index_info"}


def _make_authorizer(allow_introspection: bool):
    def authorize(action, arg1, arg2, db_name, trigger):
        if action == sqlite3.SQLITE_PRAGMA:
            if allow_introspection and (arg1 or "").lower() in _INTROSPECTION_PRAGMAS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in _WRITE_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and arg2 in {"load_extension", "readfile", "writefile"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorize


@contextmanager
def analytics_connection(url: str | None = None, allow_introspection: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a hardened, read-only connection.

    `allow_introspection=True` additionally permits the read-only PRAGMAs used
    to describe a schema. It is used by the schema inspector only — never for
    executing generated SQL, which always runs with introspection denied.
    """
    url = url or Config.ANALYTICS_DATABASE_URL
    if not Config.is_sqlite(url):
        raise NotImplementedError("Only SQLite analytics connections are implemented.")
    path = Config.sqlite_path(url)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(path).close()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.set_authorizer(_make_authorizer(allow_introspection))
    except Exception as exc:  # pragma: no cover
        conn.close()
        raise SQLSecurityError(detail=f"Could not install SQL authorizer: {exc}") from exc
    try:
        yield conn
    finally:
        conn.set_authorizer(None)
        conn.close()


def analytics_writer(url: str | None = None) -> sqlite3.Connection:
    """Writable handle used only by AInsight itself when materialising datasets."""
    url = url or Config.ANALYTICS_DATABASE_URL
    path = Config.sqlite_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn
