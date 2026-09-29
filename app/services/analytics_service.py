"""Analytics helpers used by the dashboard and the direct /api/query endpoint."""

from __future__ import annotations

import io

import pandas as pd

from app.agents import pandas_agent, sql_agent
from app.core.models import QueryPlan, ValidationError
from app.database import repository
from app.services import dataset_service
from app.utils.exceptions import AInsightError
from app.utils.logger import get_logger

log = get_logger("services.analytics")


def run_plan(plan_payload: dict, dataset_id: str) -> dict:
    """Execute a caller-supplied plan (validated first). Used by /api/query."""
    source = dataset_service.get_source(dataset_id)
    try:
        plan = QueryPlan.model_validate({**plan_payload, "dataset": source.name})
    except ValidationError as exc:
        raise AInsightError("That analysis plan is not valid.", str(exc)) from exc

    from app.ai.query_planner import validate_plan

    # A caller-supplied plan is validated as written. Unlike LLM output it is
    # never silently repaired: dropping a column the caller explicitly asked
    # for would answer a different question than the one that was posed.
    report = validate_plan(plan, source)
    if not report.valid:
        raise AInsightError("That plan does not match this dataset.", "; ".join(report.errors))

    if source.kind == "sql":
        result, _query_report, _info = sql_agent.run("(direct plan execution)", plan, source)
    else:
        result, _operation = pandas_agent.execute(plan, source)

    from app.agents.validation_agent import validate_result
    from app.agents.visualization_agent import build_figure

    result_report = validate_result(result, plan, source)
    return {
        "plan": plan.model_dump(),
        "result": {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "scalar": result.scalar,
            "execution_ms": result.execution_ms,
            "engine": result.engine,
        },
        "sql": result.sql,
        "operation": result.operation,
        "validation": result_report.model_dump(),
        "visualization": build_figure(plan, result),
    }


def dashboard_metrics() -> dict:
    metrics = repository.metrics_summary()
    datasets = repository.list_datasets()
    metrics["recent_datasets"] = [
        {
            "id": d["id"],
            "name": d["name"],
            "source_type": d["source_type"],
            "row_count": d["row_count"],
            "column_count": d["column_count"],
            "status": d["status"],
            "updated_at": d["updated_at"],
        }
        for d in datasets[:6]
    ]
    metrics["recent_queries"] = [
        {
            "id": h["id"],
            "question": h["question"],
            "dataset_name": h["dataset_name"],
            "status": h["status"],
            "total_ms": h["total_ms"],
            "created_at": h["created_at"],
        }
        for h in repository.list_history(limit=6)
    ]
    return metrics


def result_to_csv(result: dict) -> bytes:
    frame = pd.DataFrame(result.get("rows", []), columns=result.get("columns") or None)
    return frame.to_csv(index=False).encode("utf-8")


def result_to_excel(result: dict) -> bytes:
    frame = pd.DataFrame(result.get("rows", []), columns=result.get("columns") or None)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="AInsight Result")
    return buffer.getvalue()
