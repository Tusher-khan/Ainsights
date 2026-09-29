"""Query validation: security plus schema awareness.

Security is delegated to app/security/sql_security.py. This module adds the
semantic checks that prevent hallucinated tables and columns from executing.
"""

from __future__ import annotations

import re

from app.core.models import ValidationReport
from app.database.schema_inspector import describe_table, list_tables
from app.security.sql_security import enforce_limit, validate_sql, validator_backend
from app.utils.exceptions import SQLSecurityError
from app.utils.logger import get_logger

log = get_logger("database.validator")

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SQL_WORDS = {
    "select", "from", "where", "group", "by", "order", "having", "limit", "as", "and", "or",
    "not", "in", "like", "between", "is", "null", "join", "left", "right", "inner", "outer",
    "on", "with", "case", "when", "then", "else", "end", "distinct", "asc", "desc", "union",
    "all", "sum", "avg", "count", "min", "max", "round", "cast", "date", "strftime", "substr",
    "coalesce", "abs", "length", "upper", "lower", "trim", "julianday", "total", "printf",
    "integer", "real", "text", "numeric", "over", "partition", "row_number", "rank", "cte",
}


def validate_query(sql: str, allowed_tables: set[str] | None = None, max_rows: int = 5000) -> tuple[str, ValidationReport]:
    """Return (safe_sql, report). Raises SQLSecurityError when unsafe."""
    report = ValidationReport()
    tables = allowed_tables if allowed_tables is not None else set(list_tables())

    try:
        safe_sql = validate_sql(sql, tables)
        report.add("sql_security", True, f"validated with {validator_backend()}")
    except SQLSecurityError as exc:
        report.add("sql_security", False, exc.detail)
        raise

    # Column awareness: warn (not block) on identifiers absent from the schema,
    # because aliases and expressions legitimately introduce new names.
    known_columns: set[str] = set()
    for table in tables:
        try:
            known_columns |= {c["name"].lower() for c in describe_table(table, 0)["columns"]}
        except ValueError:
            continue
    aliases = {m.lower() for m in re.findall(r"\bas\s+([A-Za-z_][\w]*)", sql, re.I)}
    identifiers = {
        token.lower()
        for token in _IDENTIFIER_RE.findall(re.sub(r"'[^']*'", "", sql))
        if token.lower() not in _SQL_WORDS
    }
    unknown = identifiers - known_columns - {t.lower() for t in tables} - aliases
    if unknown:
        report.warn(f"Identifiers not found in the schema: {', '.join(sorted(unknown))}")
    report.add("schema_awareness", True, f"{len(known_columns)} known columns checked")

    limited = enforce_limit(safe_sql, max_rows)
    report.add("row_limit", True, f"result capped at {max_rows} rows")
    return limited, report
