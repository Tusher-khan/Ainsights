"""Text-to-SQL agent.

Two generation paths, one validation gate:

* `build_sql_from_plan`  — deterministic SQL assembled from the validated plan.
  Used in demo mode, as the fallback, and as the "no-LLM" baseline condition.
* `generate_sql_with_llm` — the model writes SQL grounded in the real schema.

Either way the SQL goes through `validate_query` (security + schema) before it
can execute, and a rejection is fed back to the model as a structured error for
a bounded number of retries.
"""

from __future__ import annotations

from config import Config
from app.ai import llm_provider
from app.ai.prompts import SQL_SYSTEM, sql_user_prompt
from app.core.datasource import DataSource
from app.core.models import ExecutionResult, Filter, QueryPlan, ValidationReport
from app.database.query_executor import execute_sql
from app.database.query_validator import validate_query
from app.utils.exceptions import LLMError, QueryExecutionError, SQLSecurityError
from app.utils.helpers import dumps
from app.utils.logger import get_logger

log = get_logger("agents.sql")

_AGG_SQL = {
    "sum": 'SUM({col})',
    "avg": 'AVG({col})',
    "mean": 'AVG({col})',
    "count": 'COUNT({col})',
    "count_distinct": 'COUNT(DISTINCT {col})',
    "min": 'MIN({col})',
    "max": 'MAX({col})',
    "median": 'AVG({col})',   # SQLite has no MEDIAN
    "std": 'AVG({col})',
}

_GRAIN_SQL = {
    "day": "%Y-%m-%d",
    "week": "%Y-W%W",
    "month": "%Y-%m",
    "quarter": "%Y",   # refined below
    "year": "%Y",
}


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _filter_sql(condition: Filter) -> str:
    column = _quote(condition.column)
    operator, value = condition.operator, condition.value
    if operator in {"=", "!=", ">", ">=", "<", "<="}:
        return f"{column} {operator} {_literal(value)}"
    if operator in {"in", "not_in"}:
        values = value if isinstance(value, (list, tuple)) else [value]
        joined = ", ".join(_literal(v) for v in values)
        return f"{column} {'NOT ' if operator == 'not_in' else ''}IN ({joined})"
    if operator == "contains":
        return f"{column} LIKE {_literal('%' + str(value) + '%')}"
    if operator == "not_contains":
        return f"{column} NOT LIKE {_literal('%' + str(value) + '%')}"
    if operator == "between":
        low, high = value if isinstance(value, (list, tuple)) and len(value) == 2 else (value, value)
        return f"{column} BETWEEN {_literal(low)} AND {_literal(high)}"
    if operator == "is_null":
        return f"{column} IS NULL"
    if operator == "not_null":
        return f"{column} IS NOT NULL"
    raise SQLSecurityError(f"Unsupported filter operator '{operator}'.")


def _alias(aggregation: str, measure: str) -> str:
    names = {"sum": "total", "avg": "average", "mean": "average", "count": "count",
             "count_distinct": "distinct", "min": "minimum", "max": "maximum",
             "median": "median", "std": "std_dev"}
    slug = "".join(ch if ch.isalnum() else "_" for ch in measure).strip("_").lower()
    return f"{names.get(aggregation, aggregation)}_{slug}"


def build_sql_from_plan(plan: QueryPlan, source: DataSource) -> str:
    """Deterministically compose analytical SQL from a validated plan."""
    table = _quote(source.table_name or source.name)
    select_parts: list[str] = []
    group_parts: list[str] = []

    if plan.time_column and plan.time_grain:
        fmt = _GRAIN_SQL.get(plan.time_grain, "%Y-%m")
        period = f"strftime('{fmt}', {_quote(plan.time_column)})"
        select_parts.append(f"{period} AS period")
        group_parts.append(period)

    for dimension in plan.dimensions:
        select_parts.append(_quote(dimension))
        group_parts.append(_quote(dimension))

    value_alias = None
    if plan.measures:
        for measure in plan.measures:
            template = _AGG_SQL.get(plan.aggregation, "SUM({col})")
            alias = _alias(plan.aggregation, measure)
            value_alias = value_alias or alias
            select_parts.append(f"{template.format(col=_quote(measure))} AS {alias}")
    else:
        value_alias = "row_count"
        select_parts.append("COUNT(*) AS row_count")

    sql = f"SELECT {', '.join(select_parts)} FROM {table}"
    if plan.filters:
        sql += " WHERE " + " AND ".join(_filter_sql(f) for f in plan.filters)
    if group_parts:
        sql += " GROUP BY " + ", ".join(group_parts)
    if plan.time_column and plan.time_grain:
        sql += " ORDER BY period ASC"
    elif plan.sort != "none" and value_alias:
        sql += f" ORDER BY {value_alias} {'ASC' if plan.sort == 'asc' else 'DESC'}"
    if plan.limit:
        sql += f" LIMIT {int(plan.limit)}"
    return sql


def generate_sql_with_llm(question: str, plan: QueryPlan, source: DataSource, error: str = "") -> tuple[str, str]:
    """Return (sql, method). Falls back to the deterministic builder."""
    try:
        response = llm_provider.complete(
            SQL_SYSTEM,
            sql_user_prompt(question, source.schema_text(), dumps(plan.model_dump()), error),
            json_mode=True,
        )
    except LLMError as exc:
        log.warning("SQL generation LLM call failed (%s); using deterministic builder", exc.detail)
        return build_sql_from_plan(plan, source), "deterministic"

    payload = response.json()
    if response.from_demo or not isinstance(payload, dict) or payload.get("use_deterministic_fallback"):
        return build_sql_from_plan(plan, source), "deterministic"
    sql = str(payload.get("sql", "")).strip()
    if not sql:
        return build_sql_from_plan(plan, source), "deterministic"
    return sql, "llm"


def run(question: str, plan: QueryPlan, source: DataSource) -> tuple[ExecutionResult, ValidationReport, dict]:
    """Generate, validate (with retries) and execute SQL."""
    allowed = {source.table_name or source.name}
    attempts: list[dict] = []
    error_feedback = ""
    max_attempts = max(1, Config.MAX_QUERY_RETRIES + 1)

    for attempt in range(max_attempts):
        sql, method = generate_sql_with_llm(question, plan, source, error_feedback)
        try:
            safe_sql, report = validate_query(sql, allowed, Config.MAX_RESULT_ROWS)
        except SQLSecurityError as exc:
            attempts.append({"attempt": attempt + 1, "method": method, "sql": sql, "error": exc.detail})
            error_feedback = exc.detail
            log.warning("SQL attempt %s rejected: %s", attempt + 1, exc.detail)
            if method == "deterministic":
                raise
            continue

        try:
            result = execute_sql(safe_sql)
        except QueryExecutionError as exc:
            attempts.append({"attempt": attempt + 1, "method": method, "sql": safe_sql, "error": exc.detail})
            error_feedback = exc.detail
            log.warning("SQL attempt %s failed to execute: %s", attempt + 1, exc.detail)
            if method == "deterministic":
                raise
            continue

        attempts.append({"attempt": attempt + 1, "method": method, "sql": safe_sql, "error": None})
        result.operation = safe_sql
        return result, report, {"attempts": attempts, "retries": attempt, "method": method}

    raise QueryExecutionError(
        "AInsight could not produce a valid query for that question.",
        f"all {max_attempts} SQL attempts failed: {attempts}",
    )
