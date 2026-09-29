"""Conversation memory.

Stores just enough to resolve follow-ups ("only the top 5", "as a pie chart"):
recent turns, the active dataset and the previous query plan. Raw data rows are
never stored in memory.
"""

from __future__ import annotations

from config import Config
from app.database import repository
from app.utils.logger import get_logger

log = get_logger("services.memory")

_context: dict[str, dict] = {}


def remember_turn(conversation_id: str, question: str, plan: dict, dataset_id: str, answer: str) -> None:
    entry = _context.setdefault(conversation_id, {"turns": []})
    entry["last_plan"] = plan
    entry["dataset_id"] = dataset_id
    entry["turns"].append({"question": question, "answer": answer})
    entry["turns"] = entry["turns"][-Config.MEMORY_TURNS :]


def get_context(conversation_id: str) -> dict:
    """In-process context, rehydrated from the store when the process restarted."""
    if conversation_id in _context:
        return _context[conversation_id]
    context: dict = {"turns": []}
    for message in repository.get_messages(conversation_id, limit=Config.MEMORY_TURNS * 2):
        payload = message.get("payload") or {}
        if message["role"] == "user":
            context["turns"].append({"question": message["content"], "answer": ""})
        else:
            if context["turns"]:
                context["turns"][-1]["answer"] = message["content"][:300]
            if payload.get("plan"):
                context["last_plan"] = payload["plan"]
            if payload.get("dataset_id"):
                context["dataset_id"] = payload["dataset_id"]
    _context[conversation_id] = context
    return context


def memory_text(conversation_id: str) -> str:
    """Render recent turns for the planner prompt."""
    context = get_context(conversation_id)
    lines = []
    for turn in context.get("turns", [])[-4:]:
        lines.append(f"User asked: {turn['question']}")
        if turn.get("answer"):
            lines.append(f"AInsight answered: {turn['answer'][:200]}")
    if context.get("last_plan"):
        plan = context["last_plan"]
        lines.append(
            "Previous analysis: "
            f"{plan.get('aggregation', '').upper()} of {', '.join(plan.get('measures', []))} "
            f"grouped by {', '.join(plan.get('dimensions', [])) or 'nothing'}"
            + (f", limit {plan['limit']}" if plan.get("limit") else "")
        )
    return "\n".join(lines)


def clear(conversation_id: str) -> None:
    _context.pop(conversation_id, None)
    repository.clear_conversation(conversation_id)
    log.info("Cleared memory for conversation %s", conversation_id)


def active_dataset(conversation_id: str) -> str | None:
    return get_context(conversation_id).get("dataset_id")
