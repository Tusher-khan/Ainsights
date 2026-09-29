"""Unified data source abstraction.

Everything downstream of the planner talks to a `DataSource`, not to pandas or
SQLite directly. Two implementations exist today:

* `FrameDataSource`  — CSV / Excel, executed with deterministic pandas.
* `SQLDataSource`    — a table in the analytical database, executed with
  validated read-only SQL.

Adding PostgreSQL means adding one more implementation of this interface; the
agents, validators and services need no changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.database.schema_inspector import describe_table
from app.files.dataframe_manager import frame_manager
from app.utils.exceptions import DatasetNotFoundError


@dataclass
class DataSource:
    """Common description of anything AInsight can analyse."""

    id: str
    name: str
    kind: str  # "dataframe" | "sql"
    profile: dict = field(default_factory=dict)
    table_name: str | None = None
    sheet_name: str | None = None

    # --- schema ---------------------------------------------------------
    @property
    def columns(self) -> list[str]:
        return list(self.profile.get("column_names", []))

    @property
    def measures(self) -> list[str]:
        return list(self.profile.get("measures", []))

    @property
    def dimensions(self) -> list[str]:
        return list(self.profile.get("dimensions", []))

    @property
    def date_columns(self) -> list[str]:
        return list(self.profile.get("date_columns", []))

    @property
    def row_count(self) -> int:
        return int(self.profile.get("row_count", 0))

    def column_profile(self, name: str) -> dict | None:
        for column in self.profile.get("columns", []):
            if column["name"].lower() == str(name).lower():
                return column
        return None

    def resolve_column(self, name: str) -> str | None:
        """Case/space-insensitive column resolution (LLMs get casing wrong)."""
        if not name:
            return None
        target = str(name).strip().lower().replace(" ", "").replace("_", "")
        for column in self.columns:
            candidate = column.strip().lower().replace(" ", "").replace("_", "")
            if candidate == target:
                return column
        return None

    def schema_text(self) -> str:
        """Compact schema description for prompts. Data values are samples only."""
        lines = [f"DATASET: {self.name} ({self.kind}, {self.row_count} rows)"]
        for column in self.profile.get("columns", []):
            samples = ", ".join(str(v) for v in column.get("sample_values", [])[:3])
            lines.append(
                f"- {column['name']} [{column['semantic_type']}/{column['dtype']}]"
                + (f" e.g. {samples}" if samples else "")
            )
        return "\n".join(lines)

    # --- data access ----------------------------------------------------
    def frame(self) -> pd.DataFrame:  # pragma: no cover - overridden
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "table_name": self.table_name,
            "sheet_name": self.sheet_name,
            "row_count": self.row_count,
            "columns": self.columns,
        }


@dataclass
class FrameDataSource(DataSource):
    dataset_record: dict = field(default_factory=dict)

    def frame(self) -> pd.DataFrame:
        return frame_manager.load(self.dataset_record)


@dataclass
class SQLDataSource(DataSource):
    def frame(self) -> pd.DataFrame:
        from app.database.query_executor import execute_sql

        result = execute_sql(f'SELECT * FROM "{self.table_name}" LIMIT 10000')
        return pd.DataFrame(result.rows)


def from_dataset_record(record: dict) -> DataSource:
    """Build the right DataSource for a stored dataset record."""
    if not record:
        raise DatasetNotFoundError()
    profile = record.get("profile") or {}
    if record.get("source_type") == "sqlite" and record.get("table_name"):
        return SQLDataSource(
            id=record["id"],
            name=record["name"],
            kind="sql",
            profile=profile,
            table_name=record["table_name"],
        )
    return FrameDataSource(
        id=record["id"],
        name=record["name"],
        kind="dataframe",
        profile=profile,
        sheet_name=record.get("sheet_name"),
        dataset_record=record,
    )


def from_sql_table(table: str) -> DataSource:
    """Build a DataSource for a table discovered in the analytical database."""
    info = describe_table(table, sample_rows=3)
    columns = []
    for column in info["columns"]:
        sql_type = column["type"]
        semantic = (
            "numeric"
            if any(t in sql_type for t in ("INT", "REAL", "NUM", "DEC", "FLOA", "DOUB"))
            else "datetime"
            if any(t in sql_type for t in ("DATE", "TIME"))
            else "categorical"
        )
        samples: list[Any] = [row.get(column["name"]) for row in info["sample_rows"]]
        columns.append(
            {
                "name": column["name"],
                "dtype": sql_type,
                "semantic_type": semantic,
                "null_count": 0,
                "null_percentage": 0.0,
                "unique_count": 0,
                "sample_values": [s for s in samples if s is not None][:3],
                "min": None,
                "max": None,
                "mean": None,
                "description": f"Column '{column['name']}' of SQL table {table} ({sql_type}).",
            }
        )
    profile = {
        "name": table,
        "source_type": "sqlite",
        "row_count": info["row_count"],
        "column_count": len(columns),
        "columns": columns,
        "column_names": [c["name"] for c in columns],
        "measures": [c["name"] for c in columns if c["semantic_type"] == "numeric"],
        "dimensions": [c["name"] for c in columns if c["semantic_type"] == "categorical"],
        "date_columns": [c["name"] for c in columns if c["semantic_type"] == "datetime"],
        "primary_keys": info["primary_keys"],
        "foreign_keys": info["foreign_keys"],
        "sample_rows": info["sample_rows"],
    }
    return SQLDataSource(id=f"sql_{table}", name=table, kind="sql", profile=profile, table_name=table)
