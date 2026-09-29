"""Answer and insight generation.

The headline number is always taken from the execution result. The LLM only
writes the surrounding prose, and only from facts it is handed. In demo mode a
deterministic template composes the same prose from the same facts, so the
numbers are identical either way.
"""

from __future__ import annotations

from app.ai import llm_provider
from app.ai.prompts import INSIGHT_SYSTEM, insight_user_prompt
from app.agents.validation_agent import EMPTY_MESSAGE
from app.core.datasource import DataSource
from app.core.models import ExecutionResult, QueryPlan
from app.utils.exceptions import LLMError
from app.utils.helpers import human_number
from app.utils.logger import get_logger

log = get_logger("ai.response")


def _readable(column: str) -> str:
    return column.replace("_", " ").strip().title()


def headline(result: ExecutionResult, plan: QueryPlan) -> dict | None:
    """The single most important number, if the result has one."""
    if result.row_count == 0:
        return None
    if result.row_count == 1 and len(result.columns) == 1:
        column = result.columns[0]
        value = result.rows[0][column]
        return {
            "label": _readable(column),
            "value": human_number(value) if isinstance(value, (int, float)) else str(value),
            "raw_value": value,
        }
    if result.row_count >= 1 and len(result.columns) >= 2 and plan.intent in {"ranking", "grouping", "comparison"}:
        label_column, value_column = result.columns[0], result.columns[-1]
        top = result.rows[0]
        value = top.get(value_column)
        return {
            "label": f"{_readable(value_column)} — {top.get(label_column)}",
            "value": human_number(value) if isinstance(value, (int, float)) else str(value),
            "raw_value": value,
        }
    return None


def result_summary(result: ExecutionResult, plan: QueryPlan, source: DataSource) -> str:
    """Factual summary handed to the LLM. Contains only computed values."""
    lines = [
        f"Dataset: {source.name} ({source.row_count} rows)",
        f"Operation executed: {result.operation}",
        f"Rows returned: {result.row_count}",
    ]
    if result.scalar is not None:
        lines.append(f"Computed value: {result.scalar}")
    if plan.filters:
        conditions = "; ".join(f"{f.column} {f.operator} {f.value}" for f in plan.filters)
        lines.append(f"Filters applied: {conditions}")
    if result.message:
        lines.append(result.message)
    return "\n".join(lines)


def _preview(result: ExecutionResult, limit: int = 12) -> str:
    if not result.rows:
        return "(no rows)"
    lines = [" | ".join(result.columns)]
    for row in result.rows[:limit]:
        lines.append(" | ".join(str(row.get(c, "")) for c in result.columns))
    if result.row_count > limit:
        lines.append(f"... and {result.row_count - limit} more rows")
    return "\n".join(lines)


def deterministic_insight(result: ExecutionResult, plan: QueryPlan, source: DataSource) -> str:
    """Template insight built strictly from computed values."""
    if result.row_count == 0:
        return EMPTY_MESSAGE

    measure = plan.measures[0] if plan.measures else "records"
    filters = (
        " after applying " + ", ".join(f"{f.column} {f.operator} {f.value}" for f in plan.filters)
        if plan.filters
        else ""
    )

    if result.row_count == 1 and len(result.columns) == 1:
        value = result.rows[0][result.columns[0]]
        return (
            f"{_readable(result.columns[0])} across {source.name} is {human_number(value)}{filters}. "
            f"This was computed directly from {source.row_count:,} rows using {result.operation}."
        )

    # Time series: describe direction and peak, never "leads"/"lowest" ranking.
    if plan.time_column and plan.time_grain and len(result.columns) >= 2 and result.row_count >= 2:
        period_column, value_column = result.columns[0], result.columns[-1]
        points = [r for r in result.rows if isinstance(r.get(value_column), (int, float))]
        if points:
            first, last = points[0], points[-1]
            peak = max(points, key=lambda r: r[value_column])
            trough = min(points, key=lambda r: r[value_column])
            change = last[value_column] - first[value_column]
            direction = "higher" if change > 0 else "lower" if change < 0 else "unchanged"
            pct = (abs(change) / abs(first[value_column]) * 100) if first[value_column] else None
            period_count = len({r[period_column] for r in points})
            sentences = [
                f"Across {period_count} {plan.time_grain}s, {_readable(value_column)} moved from "
                f"{human_number(first[value_column])} in {first[period_column]} to "
                f"{human_number(last[value_column])} in {last[period_column]}"
                + (f", {pct:.1f}% {direction}." if pct is not None else f" ({direction})."),
                f"The strongest {plan.time_grain} was {peak[period_column]} at {human_number(peak[value_column])}, "
                f"the weakest {trough[period_column]} at {human_number(trough[value_column])}.",
                f"Each point is {result.operation} on {source.name}.",
            ]
            return " ".join(sentences)

    if len(result.columns) >= 2 and result.row_count >= 2:
        label_column, value_column = result.columns[0], result.columns[-1]
        rows = [r for r in result.rows if isinstance(r.get(value_column), (int, float))]
        if rows:
            top, bottom = rows[0], rows[-1]
            total = sum(r[value_column] for r in rows)
            share = (top[value_column] / total * 100) if total else None
            sentences = [
                f"{top.get(label_column)} leads on {_readable(value_column)} with {human_number(top[value_column])}"
                + (f", about {share:.1f}% of the {len(rows)} groups shown" if share is not None else "")
                + f"{filters}."
            ]
            if len(rows) > 1 and bottom.get(label_column) != top.get(label_column):
                sentences.append(
                    f"The lowest in this view is {bottom.get(label_column)} at {human_number(bottom[value_column])}."
                )
            if plan.time_column and plan.time_grain:
                sentences.append(
                    f"Values are grouped by {plan.time_grain} on {plan.time_column}, so each point is a completed period."
                )
            sentences.append(f"All figures come from {result.operation} on {source.name}.")
            return " ".join(sentences)

    return (
        f"The analysis returned {result.row_count} rows from {source.name} using {result.operation}{filters}. "
        "The table below contains the computed values."
    )


def generate_insight(question: str, result: ExecutionResult, plan: QueryPlan, source: DataSource) -> tuple[str, str]:
    """Return (insight_text, method)."""
    if result.row_count == 0:
        return EMPTY_MESSAGE, "deterministic"

    try:
        response = llm_provider.complete(
            INSIGHT_SYSTEM,
            insight_user_prompt(
                question, result_summary(result, plan, source), _preview(result), source.name
            ),
        )
        if response.from_demo or not response.text.strip():
            return deterministic_insight(result, plan, source), "deterministic"
        text = response.text.strip()
        if text.startswith("{"):  # a model that ignored the prose instruction
            return deterministic_insight(result, plan, source), "deterministic"
        return text, "llm"
    except LLMError as exc:
        log.warning("Insight generation failed (%s); using deterministic summary", exc.detail)
        return deterministic_insight(result, plan, source), "deterministic"


def answer_text(result: ExecutionResult, plan: QueryPlan, source: DataSource) -> str:
    """One-line answer used in history and notifications."""
    if result.row_count == 0:
        return EMPTY_MESSAGE
    head = headline(result, plan)
    if head:
        return f"{head['label']}: {head['value']}"
    return f"{result.row_count} rows returned from {source.name}."
