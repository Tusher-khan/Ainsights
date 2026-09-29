"""SQLite schema discovery for the SQL agent.

Returns tables, columns, types, primary keys, foreign keys, row counts and
sample rows — the metadata that grounds text-to-SQL generation.
"""

from __future__ import annotations

from app.database.connection import analytics_connection
from app.utils.logger import get_logger

log = get_logger("database.inspector")

_INTERNAL_TABLES = {
    "datasets",
    "conversations",
    "messages",
    "query_history",
    "feedback",
    "sqlite_sequence",
}


def _introspect():
    return analytics_connection(allow_introspection=True)


def list_tables(include_internal: bool = False) -> list[str]:
    with _introspect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
        ).fetchall()
    tables = [r["name"] for r in rows if not r["name"].startswith("sqlite_")]
    if include_internal:
        return tables
    return [t for t in tables if t not in _INTERNAL_TABLES]


def describe_table(table: str, sample_rows: int = 3) -> dict:
    if table not in list_tables(include_internal=True):
        raise ValueError(f"Unknown table '{table}'")
    with _introspect() as conn:
        columns = [dict(r) for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
        foreign_keys = [dict(r) for r in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()]
        row_count = conn.execute(f'SELECT COUNT(*) AS c FROM "{table}"').fetchone()["c"]
        samples = [dict(r) for r in conn.execute(f'SELECT * FROM "{table}" LIMIT {int(sample_rows)}').fetchall()]
    return {
        "table": table,
        "columns": [
            {
                "name": c["name"],
                "type": (c["type"] or "TEXT").upper(),
                "nullable": not c["notnull"],
                "primary_key": bool(c["pk"]),
            }
            for c in columns
        ],
        "primary_keys": [c["name"] for c in columns if c["pk"]],
        "foreign_keys": [
            {"column": fk["from"], "references": f"{fk['table']}.{fk['to']}"} for fk in foreign_keys
        ],
        "row_count": row_count,
        "sample_rows": samples,
    }


def schema_summary(tables: list[str] | None = None) -> str:
    """Compact DDL-style summary used as LLM context (never full data)."""
    names = tables or list_tables()
    lines = []
    for name in names:
        try:
            info = describe_table(name, sample_rows=0)
        except ValueError:
            continue
        columns = ", ".join(f"{c['name']} {c['type']}" for c in info["columns"])
        lines.append(f"TABLE {name} ({columns})  -- {info['row_count']} rows")
        for fk in info["foreign_keys"]:
            lines.append(f"  FK {name}.{fk['column']} -> {fk['references']}")
    return "\n".join(lines) or "(no analytical tables registered)"


def full_schema() -> list[dict]:
    return [describe_table(t) for t in list_tables()]
