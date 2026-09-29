"""Chart helpers exposed to routes (dataset previews and re-charting)."""

from __future__ import annotations

from app.agents.visualization_agent import build_figure, choose_chart
from app.core.models import ExecutionResult, QueryPlan


def figure_from_result(result_payload: dict, chart_type: str = "bar") -> dict | None:
    """Rebuild a chart from a stored result (used by history replay)."""
    result = ExecutionResult(
        columns=result_payload.get("columns", []),
        rows=result_payload.get("rows", []),
        row_count=len(result_payload.get("rows", [])),
    )
    plan = QueryPlan(visualization=chart_type)
    return build_figure(plan, result)


__all__ = ["figure_from_result", "build_figure", "choose_chart"]
