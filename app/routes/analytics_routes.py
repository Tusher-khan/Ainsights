"""Analytics, history, feedback and download endpoints."""

from __future__ import annotations

import io

from flask import Blueprint, jsonify, request, send_file

from app.database import repository
from app.services.analytics_service import (
    dashboard_metrics,
    result_to_csv,
    result_to_excel,
    run_plan,
)
from app.utils.exceptions import AInsightError
from app.utils.logger import get_logger

analytics_bp = Blueprint("analytics", __name__, url_prefix="/api")
log = get_logger("routes.analytics")


@analytics_bp.post("/query")
def post_query():
    """Execute a structured plan directly (no LLM involved)."""
    payload = request.get_json(silent=True) or {}
    dataset_id = payload.get("dataset_id")
    if not dataset_id:
        raise AInsightError("Select a dataset before running a plan.")
    return jsonify(run_plan(payload.get("plan") or {}, dataset_id))


@analytics_bp.get("/metrics")
def get_metrics():
    return jsonify(dashboard_metrics())


@analytics_bp.get("/history")
def get_history():
    limit = min(int(request.args.get("limit", 100)), 500)
    return jsonify({"history": repository.list_history(limit)})


@analytics_bp.get("/history/<history_id>")
def get_history_entry(history_id: str):
    entry = repository.get_history_entry(history_id)
    if not entry:
        raise AInsightError("That query is no longer in your history.")
    return jsonify(entry)


@analytics_bp.delete("/history")
def clear_history():
    removed = repository.delete_history()
    return jsonify({"status": "cleared", "removed": removed})


@analytics_bp.delete("/history/<history_id>")
def delete_history_entry(history_id: str):
    removed = repository.delete_history(history_id)
    return jsonify({"status": "deleted" if removed else "missing", "id": history_id})


@analytics_bp.post("/feedback")
def post_feedback():
    payload = request.get_json(silent=True) or {}
    rating = (payload.get("rating") or "").strip().lower()
    if rating not in {"helpful", "not_helpful"}:
        raise AInsightError("Feedback must be either helpful or not_helpful.")
    feedback_id = repository.add_feedback(
        payload.get("history_id"), rating, (payload.get("comment") or "")[:1000]
    )
    return jsonify({"status": "recorded", "id": feedback_id}), 201


@analytics_bp.get("/download/<history_id>.<fmt>")
def download_result(history_id: str, fmt: str):
    entry = repository.get_history_entry(history_id)
    if not entry or not entry.get("result"):
        raise AInsightError("There is no stored result to download for that query.")
    result = entry["result"]
    if fmt == "csv":
        data, mimetype = result_to_csv(result), "text/csv"
    elif fmt in {"xlsx", "excel"}:
        data = result_to_excel(result)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        fmt = "xlsx"
    else:
        raise AInsightError("Downloads are available as CSV or Excel.")
    return send_file(
        io.BytesIO(data),
        mimetype=mimetype,
        as_attachment=True,
        download_name=f"ainsight_{history_id}.{fmt}",
    )
