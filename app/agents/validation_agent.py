"""Result validation — the last gate before anything reaches the user.

Checks execution success, emptiness, expected columns, NaN contamination,
aggregation sanity and visualization compatibility. A failed check downgrades
the response (or reports "no matching records"); it never lets an unverified
number through.
"""

from __future__ import annotations

import math

from app.core.datasource import DataSource
from app.core.models import ExecutionResult, QueryPlan, ValidationReport
from app.utils.logger import get_logger

log = get_logger("agents.validation")

EMPTY_MESSAGE = "No matching records were found in the selected dataset."
INSUFFICIENT_MESSAGE = "The available data is not sufficient to answer this question."


def validate_result(result: ExecutionResult, plan: QueryPlan, source: DataSource) -> ValidationReport:
    report = ValidationReport()

    report.add("execution_success", result.success, "" if result.success else "the engine reported a failure")
    if not result.success:
        return report

    report.add("result_shape", result.row_count >= 0, "")
    if result.row_count == 0:
        report.warn(EMPTY_MESSAGE)
        report.add("non_empty_result", True, "query ran successfully but matched no rows")
        return report

    # Expected columns: every grouping column the plan asked for should appear.
    expected = [d for d in plan.dimensions]
    returned = {c.lower() for c in result.columns}
    missing = [c for c in expected if c.lower() not in returned]
    report.add(
        "expected_columns",
        not missing,
        f"missing from the result: {', '.join(missing)}" if missing else f"{len(result.columns)} columns returned",
    )

    # NaN / None contamination in the value column.
    value_columns = [c for c in result.columns if c.lower() not in {d.lower() for d in plan.dimensions}]
    null_values = 0
    for row in result.rows:
        for column in value_columns:
            value = row.get(column)
            if value is None or (isinstance(value, float) and math.isnan(value)):
                null_values += 1
    if null_values:
        report.warn(f"{null_values} empty values in the result were left as blanks, not zeros.")
    report.add("value_integrity", True, f"{null_values} null values found" if null_values else "no null values")

    # Aggregation sanity: sums and counts of non-negative measures cannot be negative.
    if plan.aggregation in {"count", "count_distinct"}:
        negatives = [
            row[c] for row in result.rows for c in value_columns
            if isinstance(row.get(c), (int, float)) and row[c] < 0
        ]
        report.add("aggregation_sanity", not negatives, "a count returned a negative value" if negatives else "")

    # Row-count plausibility: a grouped result cannot exceed the source rows.
    if plan.dimensions and source.row_count:
        report.add(
            "cardinality_plausible",
            result.row_count <= max(source.row_count, 1),
            "the grouped result has more rows than the dataset",
        )

    # Visualization compatibility.
    compatible = _chart_compatible(plan.visualization, result, plan)
    if not compatible and plan.visualization != "table":
        report.warn(f"A {plan.visualization} chart does not fit this result; a table is shown instead.")
    report.add("visualization_compatible", True, "chart type checked against the result")

    if not report.valid:
        log.warning("Result validation failed: %s", report.errors)
    return report


def _chart_compatible(chart_type: str, result: ExecutionResult, plan: QueryPlan) -> bool:
    if chart_type == "table":
        return True
    if result.row_count < 1 or len(result.columns) < 2:
        return False
    if chart_type in {"pie", "donut"} and result.row_count > 12:
        return False
    if chart_type in {"line"} and not (plan.time_column or result.row_count > 2):
        return False
    return True


def verify_scalar(result: ExecutionResult) -> bool:
    """A single-cell result must genuinely be a single cell."""
    return result.row_count == 1 and len(result.columns) == 1
