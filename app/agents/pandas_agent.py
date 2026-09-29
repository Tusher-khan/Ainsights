"""Pandas analytics agent.

The agent translates a validated `QueryPlan` into a `PandasOperation` and then
executes it with hand-written pandas code. No LLM-generated Python is ever run:
there is no `eval`, no `exec`, and no dynamic attribute access anywhere in this
module. Anything the plan asks for that is not in the supported operation set
is rejected rather than improvised.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from config import Config
from app.core.datasource import DataSource
from app.core.models import ExecutionResult, Filter, PandasOperation, QueryPlan
from app.utils.exceptions import ColumnNotFoundError, QueryExecutionError
from app.utils.helpers import dataframe_to_records, json_safe
from app.utils.logger import get_logger

log = get_logger("agents.pandas")

_AGG_FUNCS = {
    "sum": "sum",
    "avg": "mean",
    "mean": "mean",
    "count": "count",
    "count_distinct": "nunique",
    "min": "min",
    "max": "max",
    "median": "median",
    "std": "std",
}

_GRAIN_FREQ = {"day": "D", "week": "W", "month": "MS", "quarter": "QS", "year": "YS"}
_GRAIN_LABEL = {"day": "%Y-%m-%d", "week": "%Y-W%V", "month": "%Y-%m", "quarter": "%Y-Q%q", "year": "%Y"}


def plan_to_operation(plan: QueryPlan) -> PandasOperation:
    """Derive the deterministic operation from the validated plan."""
    if plan.time_column and plan.time_grain:
        operation = "trend"
    elif plan.intent == "statistical_analysis":
        operation = "correlation" if len(plan.measures) >= 2 else "distribution"
    elif plan.intent == "descriptive_analysis" and not plan.dimensions:
        operation = "describe"
    elif plan.intent == "filtering" and not plan.dimensions and not plan.measures:
        operation = "preview"
    elif plan.dimensions:
        operation = "topn" if plan.limit else "groupby"
    elif plan.measures:
        operation = "aggregate"
    else:
        operation = "count_rows"

    return PandasOperation(
        operation=operation,
        group_by=list(plan.dimensions),
        metric=plan.measures[0] if plan.measures else None,
        metrics=list(plan.measures),
        aggregation=plan.aggregation,
        filters=list(plan.filters),
        sort=plan.sort,
        limit=plan.limit,
        time_column=plan.time_column,
        time_grain=plan.time_grain,
    )


def _apply_filter(frame: pd.DataFrame, condition: Filter) -> pd.DataFrame:
    column = condition.column
    if column not in frame.columns:
        raise ColumnNotFoundError(f"The column '{column}' is not in this dataset.")
    series = frame[column]
    operator, value = condition.operator, condition.value

    try:
        if operator == "=":
            if pd.api.types.is_numeric_dtype(series):
                return frame[series == pd.to_numeric(value, errors="coerce")]
            return frame[series.astype(str).str.lower() == str(value).lower()]
        if operator == "!=":
            if pd.api.types.is_numeric_dtype(series):
                return frame[series != pd.to_numeric(value, errors="coerce")]
            return frame[series.astype(str).str.lower() != str(value).lower()]
        if operator in {">", ">=", "<", "<="}:
            comparator = pd.to_datetime(value) if pd.api.types.is_datetime64_any_dtype(series) else pd.to_numeric(value, errors="coerce")
            return frame[{">" : series > comparator, ">=": series >= comparator,
                          "<" : series < comparator, "<=": series <= comparator}[operator]]
        if operator == "in":
            values = value if isinstance(value, (list, tuple)) else [value]
            return frame[series.astype(str).str.lower().isin([str(v).lower() for v in values])]
        if operator == "not_in":
            values = value if isinstance(value, (list, tuple)) else [value]
            return frame[~series.astype(str).str.lower().isin([str(v).lower() for v in values])]
        if operator == "contains":
            return frame[series.astype(str).str.contains(str(value), case=False, na=False, regex=False)]
        if operator == "not_contains":
            return frame[~series.astype(str).str.contains(str(value), case=False, na=False, regex=False)]
        if operator == "between":
            low, high = (value if isinstance(value, (list, tuple)) and len(value) == 2 else (None, None))
            if low is None:
                raise QueryExecutionError("A 'between' filter needs two values.")
            if pd.api.types.is_datetime64_any_dtype(series):
                low, high = pd.to_datetime(low), pd.to_datetime(high)
            else:
                low, high = pd.to_numeric(low, errors="coerce"), pd.to_numeric(high, errors="coerce")
            return frame[series.between(low, high)]
        if operator == "is_null":
            return frame[series.isna()]
        if operator == "not_null":
            return frame[series.notna()]
    except (TypeError, ValueError) as exc:
        raise QueryExecutionError(
            f"The filter on '{column}' could not be applied.", str(exc)
        ) from exc
    raise QueryExecutionError(f"Unsupported filter operator '{operator}'.")


def _aggregate_series(series: pd.Series, aggregation: str):
    func = _AGG_FUNCS.get(aggregation, "sum")
    if func in {"sum", "mean", "median", "std", "min", "max"} and not pd.api.types.is_numeric_dtype(series):
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() == 0:
            raise QueryExecutionError(
                f"'{series.name}' is not numeric, so it cannot be aggregated with {aggregation.upper()}."
            )
        series = numeric
    return {
        "sum": series.sum, "mean": series.mean, "median": series.median, "std": series.std,
        "min": series.min, "max": series.max, "count": series.count, "nunique": series.nunique,
    }[func]()


def _label(aggregation: str, metric: str | None) -> str:
    if metric is None:
        return "row_count"
    names = {"sum": "total", "avg": "average", "mean": "average", "count": "count",
             "count_distinct": "distinct", "min": "minimum", "max": "maximum",
             "median": "median", "std": "std_dev"}
    return f"{names.get(aggregation, aggregation)}_{metric}".replace(" ", "_")


def execute(plan: QueryPlan, source: DataSource) -> tuple[ExecutionResult, PandasOperation]:
    """Run the plan deterministically against the dataset."""
    operation = plan_to_operation(plan)
    started = time.perf_counter()
    frame = source.frame()
    original_rows = len(frame)

    for condition in operation.filters:
        frame = _apply_filter(frame, condition)

    metric = operation.metric
    if metric and metric not in frame.columns:
        raise ColumnNotFoundError(f"The column '{metric}' is not in this dataset.")
    value_name = _label(operation.aggregation, metric)
    truncated = False

    if operation.operation == "count_rows":
        result_frame = pd.DataFrame([{"row_count": len(frame)}])
        scalar = len(frame)

    elif operation.operation == "aggregate":
        values = {}
        for column in operation.metrics or [metric]:
            if column:
                values[_label(operation.aggregation, column)] = json_safe(
                    _aggregate_series(frame[column], operation.aggregation)
                )
        if not values:
            values = {"row_count": len(frame)}
        result_frame = pd.DataFrame([values])
        scalar = list(values.values())[0] if len(values) == 1 else None

    elif operation.operation in {"groupby", "topn"}:
        for column in operation.group_by:
            if column not in frame.columns:
                raise ColumnNotFoundError(f"The column '{column}' is not in this dataset.")
        grouped = frame.groupby(operation.group_by, dropna=False)
        if metric:
            series = grouped[metric].agg(_AGG_FUNCS.get(operation.aggregation, "sum"))
        else:
            series = grouped.size()
            value_name = "row_count"
        result_frame = series.reset_index(name=value_name) if isinstance(series, pd.Series) else series.reset_index()
        if result_frame.columns[-1] != value_name:
            result_frame = result_frame.rename(columns={result_frame.columns[-1]: value_name})
        if operation.sort != "none":
            result_frame = result_frame.sort_values(value_name, ascending=operation.sort == "asc")
        if operation.limit:
            truncated = len(result_frame) > operation.limit
            result_frame = result_frame.head(operation.limit)
        scalar = None

    elif operation.operation == "trend":
        time_column = operation.time_column
        if time_column not in frame.columns:
            raise ColumnNotFoundError(f"The column '{time_column}' is not in this dataset.")
        times = pd.to_datetime(frame[time_column], errors="coerce")
        working = frame.assign(**{"__period__": times}).dropna(subset=["__period__"])
        if working.empty:
            raise QueryExecutionError(f"'{time_column}' has no readable dates, so no trend can be computed.")
        freq = _GRAIN_FREQ.get(operation.time_grain or "month", "MS")
        periods = working["__period__"].dt.to_period(freq[0] if freq != "MS" else "M")
        working = working.assign(**{time_column + "_period": periods.astype(str)})
        group_columns = [time_column + "_period"] + list(operation.group_by)
        if metric:
            series = working.groupby(group_columns, dropna=False)[metric].agg(
                _AGG_FUNCS.get(operation.aggregation, "sum")
            )
        else:
            series = working.groupby(group_columns, dropna=False).size()
            value_name = "row_count"
        result_frame = series.reset_index(name=value_name)
        result_frame = result_frame.sort_values(time_column + "_period")
        scalar = None

    elif operation.operation == "distribution":
        if not metric:
            raise QueryExecutionError("A numeric column is needed to show a distribution.")
        series = pd.to_numeric(frame[metric], errors="coerce").dropna()
        if series.empty:
            raise QueryExecutionError(f"'{metric}' has no numeric values to describe.")
        counts, edges = np.histogram(series, bins=min(20, max(5, int(np.sqrt(len(series))))))
        result_frame = pd.DataFrame(
            {
                "bucket": [f"{edges[i]:.2f} – {edges[i+1]:.2f}" for i in range(len(counts))],
                "bucket_start": edges[:-1],
                "frequency": counts,
            }
        )
        scalar = None

    elif operation.operation == "correlation":
        numeric_columns = [c for c in (operation.metrics or source.measures) if c in frame.columns]
        numeric_frame = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
        if numeric_frame.shape[1] < 2:
            raise QueryExecutionError("At least two numeric columns are needed for a correlation.")
        matrix = numeric_frame.corr().round(4)
        result_frame = matrix.reset_index().rename(columns={"index": "column"})
        scalar = None

    elif operation.operation == "describe":
        numeric_columns = [c for c in source.measures if c in frame.columns]
        if not numeric_columns:
            result_frame = pd.DataFrame(
                [{"metric": "rows", "value": len(frame)}, {"metric": "columns", "value": frame.shape[1]}]
            )
        else:
            described = frame[numeric_columns].describe().round(4)
            result_frame = described.reset_index().rename(columns={"index": "statistic"})
        scalar = None

    else:  # preview
        limit = operation.limit or 50
        result_frame = frame.head(limit)
        truncated = len(frame) > limit
        scalar = None

    elapsed = round((time.perf_counter() - started) * 1000, 2)
    if len(result_frame) > Config.MAX_RESULT_ROWS:
        result_frame = result_frame.head(Config.MAX_RESULT_ROWS)
        truncated = True

    if scalar is None and len(result_frame) == 1 and len(result_frame.columns) == 1:
        scalar = result_frame.iloc[0, 0]

    log.info(
        "Pandas %s on %s: %s rows in (filtered from %s), %s rows out, %sms",
        operation.operation, source.name, len(frame), original_rows, len(result_frame), elapsed,
    )

    description = _describe_operation(operation, value_name)
    return (
        ExecutionResult(
            success=True,
            columns=[str(c) for c in result_frame.columns],
            rows=dataframe_to_records(result_frame),
            row_count=int(len(result_frame)),
            scalar=json_safe(scalar),
            operation=description,
            engine="pandas",
            execution_ms=elapsed,
            truncated=truncated,
            message=f"{len(frame)} of {original_rows} rows matched the filters."
            if len(frame) != original_rows
            else "",
        ),
        operation,
    )


def _describe_operation(operation: PandasOperation, value_name: str) -> str:
    """Human-readable description of exactly what was computed."""
    aggregation = operation.aggregation.upper()
    metric = operation.metric or "rows"
    if operation.operation == "count_rows":
        return "COUNT(rows)"
    if operation.operation == "aggregate":
        return f"{aggregation}({metric})"
    if operation.operation in {"groupby", "topn"}:
        text = f"{aggregation}({metric}) GROUP BY {', '.join(operation.group_by)}"
        if operation.sort != "none":
            text += f" ORDER BY {value_name} {operation.sort.upper()}"
        if operation.limit:
            text += f" LIMIT {operation.limit}"
        return text
    if operation.operation == "trend":
        return f"{aggregation}({metric}) BY {operation.time_grain}({operation.time_column})"
    if operation.operation == "distribution":
        return f"HISTOGRAM({metric})"
    if operation.operation == "correlation":
        return f"CORRELATION({', '.join(operation.metrics)})"
    if operation.operation == "describe":
        return "DESCRIBE(numeric columns)"
    return f"PREVIEW(limit {operation.limit or 50})"
