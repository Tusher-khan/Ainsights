"""Intent classification.

The LLM classifies when available; a deterministic rule engine classifies in
demo mode, when the model is unreachable, or when its answer fails validation.
The rule engine is also the baseline condition in the thesis experiments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai import llm_provider
from app.ai.prompts import INTENT_SYSTEM
from app.core.models import INTENTS
from app.utils.exceptions import LLMError
from app.utils.logger import get_logger

log = get_logger("ai.intent")


@dataclass
class IntentResult:
    intent: str
    confidence: float
    reason: str
    method: str  # "llm" | "rules"


RULES: list[tuple[str, re.Pattern]] = [
    ("general_chat", re.compile(r"^\s*(hi|hello|hey|thanks|thank you|good (morning|evening|afternoon))\b", re.I)),
    ("schema_question", re.compile(r"\b(what|which|list|show).{0,20}(columns?|fields?|schema|data types?|structure)\b", re.I)),
    ("dataset_question", re.compile(r"\b(how many rows|row count|how big|what (is|does) (this|the) dataset|what data|describe the dataset|when was.*updated)\b", re.I)),
    ("visualization", re.compile(r"\b(chart|graph|plot|visuali[sz]e|pie|bar chart|line chart|histogram|scatter|draw)\b", re.I)),
    ("time_series", re.compile(r"\b(monthly|weekly|daily|quarterly|yearly|per month|per year|over time|by month|by year|time series)\b", re.I)),
    ("trend_analysis", re.compile(r"\b(trend|growth|decline|increase|decrease|change over)\b", re.I)),
    ("ranking", re.compile(r"\b(top|bottom|highest|lowest|best|worst|rank|leading|largest|smallest|most|least)\b", re.I)),
    ("percentage_analysis", re.compile(r"\b(percent|percentage|share|proportion|ratio|% of|contribution)\b", re.I)),
    ("statistical_analysis", re.compile(r"\b(correlat\w*|distribut\w*|standard deviation|variance|median|quartile|spread|statistics)\b", re.I)),
    ("comparison", re.compile(r"\b(compare|versus|vs\.?|difference between|against)\b", re.I)),
    # "per order" / "per unit" describe a rate, not a grouping dimension.
    ("grouping", re.compile(r"\b(by\s+\w|for each|group(ed)? by|breakdown|split by|per\s+(?!order\b|unit\b|row\b|record\b|item\b|transaction\b)\w)", re.I)),
    ("filtering", re.compile(r"\b(where|only|filter|excluding|greater than|less than|between|in \d{4})\b", re.I)),
    ("simple_aggregation", re.compile(r"\b(total|sum|average|avg|mean|count|how much|how many|number of|max|min|maximum|minimum)\b", re.I)),
    ("descriptive_analysis", re.compile(r"\b(summar|overview|insight|tell me about|analy[sz]e|describe|explain the data)\b", re.I)),
]


def classify_with_rules(question: str) -> IntentResult:
    text = (question or "").strip()
    if not text:
        return IntentResult("general_chat", 0.5, "Empty question.", "rules")

    matches = [(intent, pattern) for intent, pattern in RULES if pattern.search(text)]
    if not matches:
        return IntentResult(
            "descriptive_analysis", 0.35, "No specific analytical pattern matched.", "rules"
        )

    intents = [intent for intent, _ in matches]
    # Precedence: the most specific analytical shape wins over generic ones.
    for candidate in (
        "general_chat",
        "schema_question",
        "dataset_question",
        "visualization",
        "time_series",
        "trend_analysis",
        "ranking",
        "percentage_analysis",
        "statistical_analysis",
        "comparison",
        "grouping",
        "filtering",
        "simple_aggregation",
        "descriptive_analysis",
    ):
        if candidate in intents:
            # "chart of monthly sales" is a time series rendered as a chart.
            if candidate == "visualization" and ("time_series" in intents or "trend_analysis" in intents):
                return IntentResult("time_series", 0.75, "Chart requested for a time-based breakdown.", "rules")
            if candidate == "visualization" and "ranking" in intents:
                return IntentResult("ranking", 0.75, "Chart requested for a ranking.", "rules")
            confidence = 0.8 if len(matches) == 1 else 0.7
            return IntentResult(candidate, confidence, f"Matched the '{candidate}' pattern.", "rules")
    return IntentResult("descriptive_analysis", 0.4, "Fallback classification.", "rules")


def classify(question: str) -> IntentResult:
    """Classify with the LLM, validating the label; fall back to rules."""
    try:
        response = llm_provider.complete(INTENT_SYSTEM, f"QUESTION: {question}", json_mode=True)
        payload = response.json() or {}
        if response.from_demo or payload.get("use_deterministic_fallback"):
            return classify_with_rules(question)
        intent = str(payload.get("intent", "")).strip().lower()
        if intent in INTENTS:
            return IntentResult(
                intent,
                float(payload.get("confidence", 0.7) or 0.7),
                str(payload.get("reason", ""))[:200],
                "llm",
            )
        log.warning("LLM returned unknown intent '%s'; using rules", intent)
    except LLMError as exc:
        log.warning("Intent classification via LLM failed (%s); using rules", exc.detail)
    return classify_with_rules(question)
