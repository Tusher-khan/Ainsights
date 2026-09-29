"""Health, system information and evaluation endpoints."""

from __future__ import annotations

import platform
import sys

from flask import Blueprint, jsonify, request

from config import Config
from app.ai.agent_orchestrator import workflow_backend
from app.ai.llm_provider import provider_status
from app.core.schema import PYDANTIC_AVAILABLE
from app.database.repository import metrics_summary
from app.evaluation.benchmark_questions import BENCHMARK_QUESTIONS
from app.evaluation.evaluator import run_benchmark, run_experiments
from app.rag.embeddings import get_embedder
from app.rag.vector_store import get_vector_store
from app.security.sql_security import validator_backend

system_bp = Blueprint("system", __name__, url_prefix="/api")


@system_bp.get("/health")
def health():
    return jsonify(
        {
            "status": "healthy",
            "service": "AInsight",
            "demo_mode": Config.DEMO_MODE,
            "llm_provider": provider_status()["provider"],
        }
    )


@system_bp.get("/system")
def system_info():
    store = get_vector_store()
    return jsonify(
        {
            "application": {
                "name": "AInsight",
                "positioning": "AI-Powered Business Analytics",
                "thesis_title": (
                    "Design and Development of a Hybrid AI-Powered Data Analytics Chatbot "
                    "for Natural Language Querying over Databases and Excel Files"
                ),
                "python": sys.version.split()[0],
                "platform": platform.platform(),
            },
            "configuration": Config.public_summary(),
            "llm": provider_status(),
            "components": {
                "workflow_engine": workflow_backend(),
                "sql_validator": validator_backend(),
                "vector_store": store.backend,
                "indexed_documents": store.count(),
                "embeddings": get_embedder().name,
                "schema_validation": "pydantic" if PYDANTIC_AVAILABLE else "builtin-validator",
            },
            "metrics": metrics_summary(),
        }
    )


@system_bp.get("/evaluation/questions")
def evaluation_questions():
    return jsonify({"questions": BENCHMARK_QUESTIONS, "count": len(BENCHMARK_QUESTIONS)})


@system_bp.post("/evaluation/run")
def evaluation_run():
    payload = request.get_json(silent=True) or {}
    return jsonify(run_benchmark(payload.get("dataset_id")))


@system_bp.post("/evaluation/experiments")
def evaluation_experiments():
    payload = request.get_json(silent=True) or {}
    return jsonify(run_experiments(payload.get("dataset_id")))
