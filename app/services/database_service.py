"""Analytical database services: table discovery and schema exposure."""

from __future__ import annotations

from app.core.datasource import from_sql_table
from app.database.schema_inspector import describe_table, full_schema, list_tables
from app.utils.logger import get_logger

log = get_logger("services.database")


def available_tables() -> list[dict]:
    tables = []
    for name in list_tables():
        try:
            info = describe_table(name, sample_rows=2)
            tables.append(
                {
                    "name": name,
                    "row_count": info["row_count"],
                    "column_count": len(info["columns"]),
                    "columns": [c["name"] for c in info["columns"]],
                }
            )
        except ValueError:
            continue
    return tables


def table_source(name: str):
    return from_sql_table(name)


def schema_payload() -> list[dict]:
    return full_schema()
