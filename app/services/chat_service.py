"""Chat service — the entry point for every question.

Responsibilities: screen the question, pick a data source, run the workflow,
persist the turn and history, and shape the response the UI renders.
"""

from __future__ import annotations

from app.ai.agent_orchestrator import run_analysis, state_to_response
from app.core.datasource import DataSource
from app.database import repository
from app.security.prompt_security import screen_user_question
from app.services import dataset_service, memory_service
from app.utils.exceptions import AInsightError, DatasetNotFoundError, PromptInjectionError
from app.utils.helpers import Timer, truncate
from app.utils.logger import get_logger

log = get_logger("services.chat")

MAX_QUESTION_LENGTH = 1000


def _schema_answer(question: str, source: DataSource) -> dict:
    """Answer schema/dataset questions from metadata, with no computation."""
    profile = source.profile
    lines = [
        f"{source.name} has {source.row_count:,} rows and {len(source.columns)} columns.",
    ]
    if source.measures:
        lines.append(f"Numeric measures: {', '.join(source.measures)}.")
    if source.dimensions:
        lines.append(f"Categorical dimensions: {', '.join(source.dimensions[:10])}.")
    if source.date_columns:
        lines.append(f"Date columns: {', '.join(source.date_columns)}.")
    quality = profile.get("quality") or {}
    if quality:
        lines.append(
            f"Data quality: {quality.get('missing_percentage', 0)}% of cells are missing and "
            f"{quality.get('duplicate_rows', 0)} duplicate rows were found."
        )
    return {
        "answer": f"{source.name}: {source.row_count:,} rows × {len(source.columns)} columns",
        "insight": " ".join(lines),
        "intent": "schema_question",
        "columns_table": {
            "columns": ["Column", "Type", "Missing %", "Distinct"],
            "rows": [
                {
                    "Column": c["name"],
                    "Type": c["semantic_type"],
                    "Missing %": c["null_percentage"],
                    "Distinct": c["unique_count"],
                }
                for c in profile.get("columns", [])
            ],
        },
        "source": {
            "dataset": source.name,
            "dataset_id": source.id,
            "kind": source.kind,
            "operation": "metadata lookup (no computation required)",
            "columns_used": [],
        },
        "validation": {"verified": True, "result": {"valid": True, "checks": [
            {"name": "metadata_only", "passed": True, "detail": "answered from the stored dataset profile"}
        ], "errors": [], "warnings": []}},
        "suggestions": dataset_service.starter_questions(source.id),
    }


def ask(
    question: str,
    dataset_id: str | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Answer one question end to end."""
    question = (question or "").strip()
    if not question:
        raise AInsightError("Type a question about your data to get started.")
    if len(question) > MAX_QUESTION_LENGTH:
        question = question[:MAX_QUESTION_LENGTH]

    allowed, reason = screen_user_question(question)
    if not allowed:
        raise PromptInjectionError(reason, "prompt injection screen triggered")

    conversation_id = repository.ensure_conversation(conversation_id, dataset_id)
    repository.add_message(conversation_id, "user", question, {"dataset_id": dataset_id})

    with Timer() as total:
        source, routing_reason = dataset_service.select_source(question, dataset_id, conversation_id)

        # Schema/dataset questions are answered from metadata, never computed.
        from app.ai.intent_classifier import classify_with_rules

        quick_intent = classify_with_rules(question).intent
        if quick_intent in {"schema_question", "dataset_question"}:
            payload = _schema_answer(question, source)
            payload.update(
                {
                    "conversation_id": conversation_id,
                    "routing_reason": routing_reason,
                    "stages": [
                        {"stage": "classify_intent", "status": "done", "detail": quick_intent},
                        {"stage": "retrieve_metadata", "status": "done", "detail": "dataset profile loaded"},
                        {"stage": "generate_response", "status": "done", "detail": "answered from metadata"},
                    ],
                }
            )
            repository.add_message(conversation_id, "assistant", payload["answer"], {"dataset_id": source.id})
            memory_service.remember_turn(conversation_id, question, {}, source.id, payload["answer"])
            payload["total_ms"] = 0.0
            payload["history_id"] = repository.add_history(
                {
                    "conversation_id": conversation_id,
                    "question": question,
                    "dataset_id": source.id,
                    "dataset_name": source.name,
                    "intent": quick_intent,
                    "engine": "metadata",
                    "status": "success",
                    "answer": payload["answer"],
                    "row_count": len(source.columns),
                }
            )
            return payload

        memory = memory_service.get_context(conversation_id)
        memory_text = memory_service.memory_text(conversation_id)

        try:
            state = run_analysis(question, source, conversation_id, memory, memory_text)
        except AInsightError as exc:
            repository.add_history(
                {
                    "conversation_id": conversation_id,
                    "question": question,
                    "dataset_id": source.id,
                    "dataset_name": source.name,
                    "status": "error",
                    "error": exc.user_message,
                    "engine": source.kind,
                }
            )
            repository.add_message(conversation_id, "assistant", exc.user_message, {"error": True})
            raise

        payload = state_to_response(state)

    payload["conversation_id"] = conversation_id
    payload["routing_reason"] = routing_reason
    payload["total_ms"] = total.ms
    payload["suggestions"] = _followups(state)

    repository.add_message(
        conversation_id,
        "assistant",
        payload.get("answer", ""),
        {"dataset_id": source.id, "plan": payload.get("plan")},
    )
    payload["history_id"] = repository.add_history(
        {
            "conversation_id": conversation_id,
            "question": question,
            "dataset_id": source.id,
            "dataset_name": source.name,
            "intent": payload.get("intent"),
            "engine": (payload.get("result") or {}).get("engine", "pandas"),
            "sql": payload.get("sql"),
            "plan": payload.get("plan"),
            "status": "success",
            "row_count": (payload.get("result") or {}).get("row_count", 0),
            "execution_ms": (payload.get("result") or {}).get("execution_ms", 0),
            "total_ms": total.ms,
            "answer": truncate(payload.get("answer", ""), 300),
            "result": payload.get("result"),
        }
    )
    return payload


def _followups(state) -> list[str]:
    """Suggest next questions from the plan that just ran."""
    plan = state.plan
    if not plan:
        return dataset_service.starter_questions(state.source.id)
    suggestions = []
    if plan.dimensions and plan.measures:
        suggestions.append(f"Show only the top 5 {plan.dimensions[0]}")
        suggestions.append(f"Show this as a chart")
    if state.source.date_columns and not plan.time_column:
        suggestions.append(f"Show monthly {plan.measures[0] if plan.measures else 'totals'} trends")
    other_measures = [m for m in state.source.measures if m not in plan.measures]
    if other_measures:
        suggestions.append(f"Do the same for {other_measures[0]}")
    return suggestions[:4] or dataset_service.starter_questions(state.source.id)


def conversation_messages(conversation_id: str) -> list[dict]:
    return repository.get_messages(conversation_id, limit=50)


def reset_conversation(conversation_id: str) -> None:
    memory_service.clear(conversation_id)
