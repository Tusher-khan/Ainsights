"""Deterministic SQL execution against a read-only connection."""

from __future__ import annotations

import sqlite3
import time

import pandas as pd

from config import Config
from app.core.models import ExecutionResult
from app.database.connection import analytics_connection
from app.utils.exceptions import QueryExecutionError
from app.utils.helpers import dataframe_to_records
from app.utils.logger import get_logger

log = get_logger("database.executor")


def execute_sql(sql: str, max_rows: int | None = None) -> ExecutionResult:
    """Run validated SQL. The database — not the LLM — produces the numbers."""
    max_rows = max_rows or Config.MAX_RESULT_ROWS
    started = time.perf_counter()
    try:
        with analytics_connection() as conn:
            conn.execute(f"PRAGMA busy_timeout = {Config.QUERY_TIMEOUT_SECONDS * 1000}")
            cursor = conn.execute(sql)
            columns = [d[0] for d in (cursor.description or [])]
            rows = cursor.fetchmany(max_rows + 1)
    except sqlite3.OperationalError as exc:
        raise QueryExecutionError(
            "The analysis could not run against this data source.", f"{exc} | sql={sql}"
        ) from exc
    except sqlite3.DatabaseError as exc:
        raise QueryExecutionError("The database rejected that query.", str(exc)) from exc

    truncated = len(rows) > max_rows
    records = [dict(r) for r in rows[:max_rows]]
    frame = pd.DataFrame(records, columns=columns or None)
    elapsed = round((time.perf_counter() - started) * 1000, 2)
    log.info("SQL executed in %sms returning %s rows", elapsed, len(records))

    scalar = None
    if len(frame) == 1 and len(frame.columns) == 1:
        scalar = frame.iloc[0, 0]

    return ExecutionResult(
        success=True,
        columns=[str(c) for c in frame.columns],
        rows=dataframe_to_records(frame),
        row_count=len(frame),
        scalar=scalar,
        sql=sql,
        operation="sql",
        engine="sql",
        execution_ms=elapsed,
        truncated=truncated,
    )
