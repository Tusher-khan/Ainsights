"""Visualization agent.

Builds Plotly figure specifications **from executed results only**. The LLM
picks a chart type; it never supplies data points. If the chosen chart does not
fit the shape of the result, the agent downgrades to a table rather than
inventing a compatible-looking figure.
"""

from __future__ import annotations

from config import Config
from app.core.models import ChartSpec, ExecutionResult, QueryPlan
from app.utils.helpers import human_number
from app.utils.logger import get_logger

log = get_logger("agents.visualization")

PALETTE = ["#2F5AE0", "#128A6E", "#C4622D", "#6D4AC4", "#0E7490", "#B2295C", "#4B5563", "#9A7B12"]

INTENT_CHART = {
    "comparison": "bar",
    "grouping": "bar",
    "ranking": "horizontal_bar",
    "trend_analysis": "line",
    "time_series": "line",
    "statistical_analysis": "histogram",
    "percentage_analysis": "pie",
    "simple_aggregation": "table",
}


def choose_chart(plan: QueryPlan, result: ExecutionResult) -> str:
    """Map intent and result shape to a chart type that actually fits."""
    requested = plan.visualization or INTENT_CHART.get(plan.intent, "table")
    if result.row_count == 0 or len(result.columns) < 2:
        return "table"
    if requested in {"pie", "donut"} and (result.row_count > 12 or result.row_count < 2):
        return "bar"
    if requested == "line" and result.row_count < 2:
        return "bar"
    if requested == "scatter" and len(result.columns) < 2:
        return "table"
    if requested == "horizontal_bar" and result.row_count > 30:
        return "bar"
    return requested


def build_figure(plan: QueryPlan, result: ExecutionResult) -> dict | None:
    """Return a Plotly figure dict, or None when a table is the honest choice."""
    chart_type = choose_chart(plan, result)
    if chart_type == "table" or not result.rows:
        return None

    columns = result.columns
    label_column = columns[0]
    value_column = next(
        (c for c in columns[1:] if any(isinstance(r.get(c), (int, float)) for r in result.rows)),
        columns[-1] if len(columns) > 1 else None,
    )
    if value_column is None:
        return None

    rows = result.rows[: Config.MAX_CHART_POINTS]
    labels = [str(r.get(label_column, "")) for r in rows]
    values = [r.get(value_column) for r in rows]
    if all(v is None for v in values):
        return None

    title = _title(plan, value_column, label_column)
    spec = ChartSpec(
        chart_type=chart_type, x=label_column, y=value_column, title=title,
        x_title=label_column, y_title=value_column,
        orientation="h" if chart_type == "horizontal_bar" else "v",
    )

    if chart_type in {"bar", "horizontal_bar"}:
        horizontal = chart_type == "horizontal_bar"
        ordered = list(reversed(list(zip(labels, values)))) if horizontal else list(zip(labels, values))
        labels = [l for l, _ in ordered]
        values = [v for _, v in ordered]
        trace = {
            "type": "bar",
            "orientation": "h" if horizontal else "v",
            "x": values if horizontal else labels,
            "y": labels if horizontal else values,
            "marker": {"color": PALETTE[0]},
            "hovertemplate": "%{label}: %{value}<extra></extra>" if horizontal else None,
            "text": [human_number(v) for v in values],
            "textposition": "auto",
        }
    elif chart_type == "line":
        trace = {
            "type": "scatter", "mode": "lines+markers", "x": labels, "y": values,
            "line": {"color": PALETTE[0], "width": 2.5}, "marker": {"size": 6},
        }
    elif chart_type in {"pie", "donut"}:
        trace = {
            "type": "pie", "labels": labels, "values": values,
            "hole": 0.55 if chart_type == "donut" else 0,
            "marker": {"colors": PALETTE},
            "textinfo": "label+percent",
        }
    elif chart_type == "scatter":
        numeric_columns = [c for c in columns if any(isinstance(r.get(c), (int, float)) for r in rows)]
        if len(numeric_columns) < 2:
            return None
        spec.x, spec.y = numeric_columns[0], numeric_columns[1]
        trace = {
            "type": "scatter", "mode": "markers",
            "x": [r.get(spec.x) for r in rows], "y": [r.get(spec.y) for r in rows],
            "marker": {"color": PALETTE[0], "size": 8, "opacity": 0.75},
        }
    elif chart_type == "histogram":
        trace = {"type": "bar", "x": labels, "y": values, "marker": {"color": PALETTE[0]}}
    else:
        return None

    trace = {k: v for k, v in trace.items() if v is not None}
    figure = {
        "data": [trace],
        "layout": {
            "title": {"text": title, "font": {"size": 15}},
            "margin": {"l": 60, "r": 20, "t": 48, "b": 60},
            "height": 380,
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "showlegend": chart_type in {"pie", "donut"},
            "xaxis": {"title": {"text": spec.x_title}, "automargin": True},
            "yaxis": {"title": {"text": spec.y_title}, "automargin": True},
            "font": {"family": "Inter, system-ui, sans-serif", "size": 12},
        },
        "config": {"displayModeBar": False, "responsive": True},
    }
    if chart_type in {"pie", "donut"}:
        figure["layout"].pop("xaxis", None)
        figure["layout"].pop("yaxis", None)
    if chart_type == "horizontal_bar":
        figure["layout"]["xaxis"]["title"]["text"] = value_column
        figure["layout"]["yaxis"]["title"]["text"] = label_column

    log.info("Built %s chart with %s points", chart_type, len(rows))
    return {"figure": figure, "spec": spec.model_dump(), "point_count": len(rows)}


def _title(plan: QueryPlan, value_column: str, label_column: str) -> str:
    readable = value_column.replace("_", " ").title()
    if plan.time_column and plan.time_grain:
        return f"{readable} by {plan.time_grain}"
    return f"{readable} by {label_column}"
