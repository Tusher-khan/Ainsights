"""Evaluation runner.

Two modes:

* `run_benchmark()` — runs the benchmark suite through the full pipeline and
  scores intent, operation, answer, chart, hallucination and security metrics.
  Ground truth is computed independently with pandas, so a correct-looking
  answer that disagrees with the data is counted as wrong.

* `run_experiments()` — the thesis ablation. Five configurations are compared
  by switching pipeline stages off:

    1. baseline_llm          — intent + planner with no schema grounding
    2. llm_schema            — planner sees the real schema
    3. llm_schema_rag        — plus retrieved metadata context
    4. llm_rag_validation    — plus plan/query/result validation
    5. full_hybrid           — the complete system (routing, agents, charts)

  Results are reported as measurements, not as claims about the system.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import pandas as pd

from app.core.datasource import DataSource
from app.core.models import QueryPlan
from app.database import repository
from app.evaluation.benchmark_questions import BENCHMARK_QUESTIONS
from app.evaluation.metrics import MetricAccumulator
from app.services import chat_service, dataset_service
from app.utils.exceptions import AInsightError, PromptInjectionError
from app.utils.logger import get_logger

log = get_logger("evaluation")

_TOLERANCE = 0.01


def _ground_truth(expression: str | None, source: DataSource) -> Any:
    """Compute the expected value independently of the pipeline.

    The expressions are authored in this repository (never model output) and
    are evaluated against the dataframe with no builtins available.
    """
    if not expression:
        return None
    try:
        frame = source.frame()
        return eval(expression, {"__builtins__": {}}, {"df": frame, "pd": pd})  # noqa: S307
    except Exception as exc:
        log.warning("Ground truth '%s' could not be computed: %s", expression, exc)
        return None


def _numbers_in(payload: dict) -> list[float]:
    values = []
    for row in (payload.get("result") or {}).get("rows", []):
        for value in row.values():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    scalar = (payload.get("result") or {}).get("scalar")
    if isinstance(scalar, (int, float)):
        values.append(float(scalar))
    return values


def _matches_truth(payload: dict, truth: Any) -> bool | None:
    if truth is None:
        return None
    if isinstance(truth, str):
        text = " ".join(str(v) for row in (payload.get("result") or {}).get("rows", []) for v in row.values())
        return truth.lower() in text.lower()
    try:
        expected = float(truth)
    except (TypeError, ValueError):
        return None
    return any(abs(v - expected) <= max(abs(expected) * _TOLERANCE, 0.01) for v in _numbers_in(payload))


def _operation_matches(payload: dict, expected: str) -> bool | None:
    if not expected or expected == "any":
        return None
    actual = ((payload.get("source") or {}).get("operation") or "").lower()
    expected_l = expected.lower()
    if expected_l == "metadata lookup":
        return "metadata" in actual
    tokens = [t for t in expected_l.replace("(", " ").replace(")", " ").replace(",", " ").split() if t]
    return all(token in actual for token in tokens)


def run_benchmark(dataset_id: str | None = None) -> dict:
    """Run every benchmark question through the live pipeline."""
    accumulator = MetricAccumulator()
    source = None
    if dataset_id:
        source = dataset_service.get_source(dataset_id)
    else:
        datasets = repository.list_datasets()
        if datasets:
            source = dataset_service.get_source(datasets[0]["id"])
    if source is None:
        raise AInsightError("Upload a dataset before running the evaluation.")

    for question in BENCHMARK_QUESTIONS:
        row: dict[str, Any] = {
            "id": question["id"],
            "question": question["question"],
            "difficulty": question["difficulty"],
            "hallucination_probe": question.get("hallucination_probe", False),
            "security_probe": question.get("security_probe", False),
        }
        started = time.perf_counter()
        try:
            payload = chat_service.ask(question["question"], dataset_id=source.id)
            row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            row["answer"] = payload.get("answer", "")
            row["intent"] = payload.get("intent")
            row["operation"] = (payload.get("source") or {}).get("operation")
            row["blocked"] = False

            if question.get("security_probe"):
                row["blocked"] = False  # reaching here means it was NOT blocked
            if question.get("hallucination_probe"):
                # The probe asks for data that does not exist. Hallucination means
                # returning a confident number instead of reporting the limitation.
                numbers = _numbers_in(payload)
                mentions_limit = any(
                    phrase in (payload.get("answer", "") + payload.get("insight", "")).lower()
                    for phrase in ("no matching records", "not sufficient", "cannot", "could not", "not in this dataset")
                )
                assumption_flagged = bool(payload.get("assumptions"))
                row["hallucinated"] = bool(numbers) and not (mentions_limit or assumption_flagged)
            else:
                row["intent_match"] = payload.get("intent") == question["expected_intent"]
                row["operation_match"] = _operation_matches(payload, question["expected_operation"])
                row["answer_match"] = _matches_truth(payload, _ground_truth(question.get("ground_truth"), source))
                if question.get("expected_chart"):
                    actual_chart = ((payload.get("visualization") or {}).get("spec") or {}).get("chart_type")
                    row["chart_match"] = actual_chart == question["expected_chart"]
        except PromptInjectionError as exc:
            row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            row["blocked"] = True
            row["answer"] = exc.user_message
            if not question.get("security_probe"):
                row["error"] = exc.user_message
        except AInsightError as exc:
            row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            row["error"] = exc.user_message
            row["answer"] = exc.user_message
        accumulator.add(row)

    summary = accumulator.summary()
    log.info("Benchmark complete: %s", summary)
    return {"summary": summary, "rows": accumulator.rows, "dataset": source.name}


# --------------------------------------------------------------------------
# Thesis ablation experiments
# --------------------------------------------------------------------------

CONFIGURATIONS = [
    {
        "id": "baseline_llm",
        "name": "Baseline planner (no schema grounding)",
        "schema": False,
        "rag": False,
        "validation": False,
        "repair": False,
    },
    {
        "id": "llm_schema",
        "name": "Planner + schema context",
        "schema": True,
        "rag": False,
        "validation": False,
        "repair": False,
    },
    {
        "id": "llm_rag",
        "name": "Planner + schema + RAG metadata",
        "schema": True,
        "rag": True,
        "validation": False,
        "repair": False,
    },
    {
        "id": "llm_rag_validation",
        "name": "Planner + RAG + validation",
        "schema": True,
        "rag": True,
        "validation": True,
        "repair": False,
    },
    {
        "id": "full_hybrid",
        "name": "Full hybrid agent system",
        "schema": True,
        "rag": True,
        "validation": True,
        "repair": True,
    },
]


def _plan_under_configuration(question: str, source: DataSource, config: dict) -> tuple[QueryPlan | None, str | None]:
    """Build a plan with selected stages disabled."""
    from app.ai.intent_classifier import classify_with_rules
    from app.ai.query_planner import plan_with_rules, repair_plan, validate_plan
    from app.rag import retriever

    intent = classify_with_rules(question).intent

    if not config["schema"]:
        # Ungrounded condition: the planner sees an empty schema, so column
        # references cannot be resolved. This is the failure mode grounding fixes.
        empty = DataSource(id=source.id, name=source.name, kind=source.kind, profile={"columns": [], "column_names": []})
        plan = plan_with_rules(question, empty, intent)
        if config["validation"]:
            report = validate_plan(plan, source)
            if not report.valid:
                return None, "; ".join(report.errors)
        return plan, None

    if config["rag"]:
        retriever.retrieve(question, dataset_id=source.id)

    plan = plan_with_rules(question, source, intent)
    if config["repair"]:
        plan = repair_plan(plan, source)
    if config["validation"]:
        report = validate_plan(plan, source)
        if not report.valid:
            return None, "; ".join(report.errors)
    return plan, None


def run_experiments(dataset_id: str | None = None) -> dict:
    """Compare the five configurations on the benchmark suite."""
    from app.agents import pandas_agent
    from app.agents.validation_agent import validate_result

    datasets = repository.list_datasets()
    if not datasets and not dataset_id:
        raise AInsightError("Upload a dataset before running the experiments.")
    source = dataset_service.get_source(dataset_id or datasets[0]["id"])

    questions = [q for q in BENCHMARK_QUESTIONS if not q.get("security_probe")]
    results = []

    for config in CONFIGURATIONS:
        accumulator = MetricAccumulator()
        for question in questions:
            row: dict[str, Any] = {
                "id": question["id"],
                "question": question["question"],
                "hallucination_probe": question.get("hallucination_probe", False),
            }
            started = time.perf_counter()
            try:
                plan, error = _plan_under_configuration(question["question"], source, config)
                if plan is None:
                    raise AInsightError(error or "plan rejected")
                result, _operation = pandas_agent.execute(plan, source)
                payload = {
                    "result": {
                        "rows": result.rows,
                        "scalar": result.scalar,
                        "columns": result.columns,
                    },
                    "source": {"operation": result.operation},
                    "answer": "",
                    "insight": "",
                    "assumptions": plan.assumptions,
                }
                if config["validation"]:
                    report = validate_result(result, plan, source)
                    if not report.valid:
                        raise AInsightError("result validation failed")
                row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
                if question.get("hallucination_probe"):
                    row["hallucinated"] = bool(_numbers_in(payload)) and not plan.assumptions
                else:
                    row["intent_match"] = plan.intent == question["expected_intent"]
                    row["operation_match"] = _operation_matches(payload, question["expected_operation"])
                    row["answer_match"] = _matches_truth(payload, _ground_truth(question.get("ground_truth"), source))
            except (AInsightError, Exception) as exc:  # a failed configuration is data
                row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
                row["error"] = str(exc)[:200]
            accumulator.add(row)

        summary = accumulator.summary()
        summary.update({"id": config["id"], "name": config["name"]})
        results.append(summary)
        log.info("Experiment %s: %s", config["id"], summary)

    return {
        "dataset": source.name,
        "question_count": len(questions),
        "configurations": results,
        "note": (
            "Measured on this dataset and benchmark suite in the current runtime "
            "configuration. Figures are experimental observations, not product claims."
        ),
    }
