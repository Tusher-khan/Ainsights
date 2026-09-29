"""Query planning.

Produces a `QueryPlan` — the structured description of the analysis to run.

Two planners share one output contract:

* `plan_with_rules`  — deterministic; parses the question against the real
  schema. Powers DEMO_MODE and every fallback path, and is the baseline
  condition in the thesis experiments.
* `plan_with_llm`    — prompts the model, then repairs and validates its JSON
  against the actual dataset schema. Hallucinated columns are dropped, not
  executed.

`validate_plan` is the gate: a plan that references anything not present in the
dataset never reaches an execution engine.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai import llm_provider
from app.ai.prompts import PLANNER_SYSTEM, planner_user_prompt
from app.core.datasource import DataSource
from app.core.models import (
    ANALYTICAL_INTENTS,
    Filter,
    QueryPlan,
    ValidationError,
    ValidationReport,
)
from app.utils.exceptions import LLMError, PlanValidationError
from app.utils.logger import get_logger

log = get_logger("ai.planner")

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# Explicit aggregation words always win.
_AGG_PATTERNS = [
    ("count_distinct", re.compile(r"\b(how many (different|distinct|unique)|distinct count|unique count)\b", re.I)),
    ("avg", re.compile(r"\b(average|avg|mean|typical)\b", re.I)),
    ("median", re.compile(r"\bmedian\b", re.I)),
    ("std", re.compile(r"\b(standard deviation|std|variability|variance)\b", re.I)),
    ("min", re.compile(r"\b(minimum|min|cheapest)\b", re.I)),
    ("max", re.compile(r"\b(maximum|max|peak|most expensive)\b", re.I)),
    ("count", re.compile(r"\b(how many|number of|count of|count)\b", re.I)),
    ("sum", re.compile(r"\b(total|sum|overall|combined|altogether)\b", re.I)),
]

# Superlatives describe a ranking ("which country has the highest sales"), not a
# per-group MAX. They only become MIN/MAX when nothing is grouped.
_SUPERLATIVE_MAX = re.compile(r"\b(highest|largest|biggest|best|most|top|leading)\b", re.I)
_SUPERLATIVE_MIN = re.compile(r"\b(lowest|smallest|worst|least|bottom)\b", re.I)

_MEASURE_PRIORITY = re.compile(
    r"(sales|revenue|profit|amount|total|price|cost|quantity|qty|units|value|spend|margin|score)", re.I
)


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _stem(word: str) -> str:
    """Crude singularisation so 'categories' matches the column 'Category'."""
    word = _normalise(word)
    for suffix, replacement in (("ies", "y"), ("ses", "s"), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)] + replacement
    return word


def _mentions(question: str, column: str) -> bool:
    """Does the question mention this column (case, separators and plurals)?"""
    q_norm = _normalise(question)
    c_norm = _normalise(column)
    if not c_norm:
        return False
    if c_norm in q_norm:
        return True
    question_stems = {_stem(w) for w in re.split(r"[^A-Za-z0-9]+", question) if w}
    column_words = [w for w in re.split(r"[^A-Za-z0-9]+", column) if len(w) > 2]
    if column_words and all(_stem(w) in question_stems for w in column_words):
        return True
    return False


def _detect_limit(question: str) -> int | None:
    match = re.search(r"\b(?:top|bottom|first|last|best|worst)\s+(\d{1,3})\b", question, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(?:top|bottom|first|last)\s+(" + "|".join(_NUMBER_WORDS) + r")\b", question, re.I)
    if match:
        return _NUMBER_WORDS[match.group(1).lower()]
    if re.search(r"\b(top|bottom|best|worst|highest|lowest|leading)\b", question, re.I):
        return 10
    return None


def _detect_aggregation(question: str, intent: str, has_dimension: bool = False) -> str:
    for aggregation, pattern in _AGG_PATTERNS:
        if pattern.search(question):
            return aggregation
    if not has_dimension:
        if _SUPERLATIVE_MAX.search(question):
            return "max"
        if _SUPERLATIVE_MIN.search(question):
            return "min"
    return "count" if intent == "filtering" else "sum"


def _detect_grain(question: str) -> str | None:
    for grain, pattern in (
        ("day", r"\b(daily|per day|by day)\b"),
        ("week", r"\b(weekly|per week|by week)\b"),
        ("month", r"\b(monthly|per month|by month|month over month)\b"),
        ("quarter", r"\b(quarterly|per quarter|by quarter)\b"),
        ("year", r"\b(yearly|annual|per year|by year|year over year)\b"),
    ):
        if re.search(pattern, question, re.I):
            return grain
    return None


def _detect_chart(question: str, intent: str, has_dimension: bool, has_time: bool) -> str:
    explicit = {
        "pie": r"\bpie\b", "donut": r"\bdonut|doughnut\b", "line": r"\bline chart|line graph\b",
        "scatter": r"\bscatter|relationship between\b", "histogram": r"\bhistogram|distribution of\b",
        "horizontal_bar": r"\bhorizontal bar\b", "bar": r"\bbar chart|bar graph\b",
    }
    for chart, pattern in explicit.items():
        if re.search(pattern, question, re.I):
            return chart
    if intent in {"time_series", "trend_analysis"} or has_time:
        return "line"
    if intent == "ranking":
        return "horizontal_bar"
    if intent == "statistical_analysis":
        return "histogram"
    if intent in {"percentage_analysis"} and has_dimension:
        return "pie"
    if intent in {"grouping", "comparison", "visualization"} and has_dimension:
        return "bar"
    return "table"


def _detect_filters(question: str, source: DataSource) -> list[Filter]:
    """Literal value matching against known categorical values only."""
    filters: list[Filter] = []
    lowered = question.lower()
    for column in source.profile.get("columns", []):
        if column["semantic_type"] not in {"categorical", "boolean"}:
            continue
        if column["unique_count"] > 200:
            continue
        for value in column.get("sample_values", []):
            text = str(value).strip()
            if len(text) < 2 or (len(text) < 3 and not text.isupper()):
                continue
            if re.search(rf"\b{re.escape(text.lower())}\b", lowered):
                filters.append(Filter(column=column["name"], operator="=", value=value))
                break
    year = re.search(r"\b(in|for|during)\s+(20\d{2})\b", question, re.I)
    if year and source.date_columns:
        filters.append(
            Filter(column=source.date_columns[0], operator="between", value=[f"{year.group(2)}-01-01", f"{year.group(2)}-12-31"])
        )
    return filters[:3]


def plan_with_rules(question: str, source: DataSource, intent: str, memory: dict | None = None) -> QueryPlan:
    """Deterministic planner grounded in the dataset's real schema."""
    memory = memory or {}
    measures_available = source.measures
    dimensions_available = [
        c["name"]
        for c in source.profile.get("columns", [])
        if c["semantic_type"] in {"categorical", "boolean"} and 1 <= c["unique_count"] <= 500
    ]

    mentioned_measures = [m for m in measures_available if _mentions(question, m)]
    mentioned_dimensions = [d for d in dimensions_available if _mentions(question, d)]
    mentioned_dates = [d for d in source.date_columns if _mentions(question, d)]

    assumptions: list[str] = []

    # A question that names a measure is analytical even if no keyword matched.
    if intent in {"descriptive_analysis", "dataset_question"}:
        if re.search(r"\b(how many (rows|records|entries)|row count)\b", question, re.I):
            intent = "simple_aggregation"
        elif any(_mentions(question, m) for m in measures_available):
            intent = "grouping" if any(_mentions(question, d) for d in dimensions_available) else "simple_aggregation"

    # Follow-up resolution: inherit the previous plan's shape when the question
    # is a refinement ("only the top 5", "as a pie chart", "what about 2023?").
    previous = memory.get("last_plan") or {}
    # A refinement names no new measure, dimension, date column or time grain —
    # it only adjusts the previous analysis ("only the top 5", "as a pie chart").
    introduces_new_subject = bool(
        mentioned_measures or mentioned_dimensions or mentioned_dates or _detect_grain(question)
    )
    is_followup = bool(previous) and not introduces_new_subject and (
        len(question.split()) <= 8
        or re.search(r"^(and|also|now|what about|only|just|instead|show (me )?(the )?(top|bottom))\b", question.strip(), re.I)
    )
    if is_followup:
        if previous.get("measures"):
            mentioned_measures = [m for m in previous["measures"] if m in measures_available]
            if mentioned_measures:
                assumptions.append(f"Continuing the previous analysis of {mentioned_measures[0]}.")
        if previous.get("dimensions"):
            mentioned_dimensions = [d for d in previous["dimensions"] if d in dimensions_available]
        if previous.get("time_column") and previous.get("time_grain"):
            mentioned_dates = [previous["time_column"]] if previous["time_column"] in source.date_columns else []

    # A bare row-count question needs no measure at all.
    wants_row_count = bool(
        re.search(r"\b(how many (rows|records|entries)|row count|number of (rows|records))\b", question, re.I)
    )

    measures: list[str] = list(mentioned_measures)
    if not measures and not wants_row_count and measures_available:
        measures = [next((m for m in measures_available if _MEASURE_PRIORITY.search(m)), measures_available[0])]
        if intent in ANALYTICAL_INTENTS - {"descriptive_analysis"}:
            assumptions.append(f"No measure was named, so '{measures[0]}' was used.")

    dimensions: list[str] = []
    if intent in {"grouping", "ranking", "comparison", "percentage_analysis", "visualization"} or mentioned_dimensions:
        dimensions = mentioned_dimensions[:2]
        if not dimensions and intent in {"ranking", "grouping"} and dimensions_available:
            best = min(dimensions_available, key=lambda d: abs(_dimension_cardinality(source, d) - 12))
            dimensions = [best]
            assumptions.append(f"No grouping column was named, so results are grouped by '{best}'.")

    aggregation = "count" if wants_row_count else _detect_aggregation(question, intent, bool(dimensions))
    if wants_row_count:
        measures = []

    time_column = mentioned_dates[0] if mentioned_dates else (source.date_columns[0] if source.date_columns else None)
    grain = _detect_grain(question)
    if intent in {"time_series", "trend_analysis"} and time_column:
        grain = grain or "month"
        assumptions.append(f"Trend calculated on '{time_column}' by {grain}.") if not mentioned_dates else None
    elif intent not in {"time_series", "trend_analysis"} and not grain:
        time_column = time_column if mentioned_dates else None

    if is_followup and not grain and previous.get("time_grain") and time_column:
        grain = previous["time_grain"]

    limit = _detect_limit(question)
    if is_followup and limit is None and previous.get("limit"):
        limit = previous["limit"]

    chart = _detect_chart(question, intent, bool(dimensions), bool(grain and time_column))
    if is_followup and chart == "table" and previous.get("visualization"):
        chart = previous["visualization"]

    plan = QueryPlan(
        intent=intent,
        dataset=source.name,
        dimensions=dimensions,
        measures=measures[:2],
        filters=_detect_filters(question, source),
        aggregation=aggregation,
        sort="asc" if re.search(r"\b(bottom|lowest|worst|smallest|least)\b", question, re.I) else "desc",
        limit=limit,
        time_column=time_column if (grain or intent in {"time_series", "trend_analysis"}) else None,
        time_grain=grain if time_column else None,
        visualization=chart,
        source_type="sql" if source.kind == "sql" else "dataframe",
        reasoning="Deterministic plan derived from the question and the dataset schema.",
        assumptions=[a for a in assumptions if a],
    )
    log.info("Rule planner produced: intent=%s dims=%s measures=%s", plan.intent, plan.dimensions, plan.measures)
    return plan


def _dimension_cardinality(source: DataSource, column: str) -> int:
    profile = source.column_profile(column)
    return profile["unique_count"] if profile else 999


def plan_with_llm(
    question: str,
    source: DataSource,
    intent: str,
    rag_context: str = "",
    memory_text: str = "",
) -> tuple[QueryPlan, str]:
    """Ask the model to plan; repair the JSON against the real schema."""
    try:
        response = llm_provider.complete(
            PLANNER_SYSTEM,
            planner_user_prompt(question, source.schema_text(), rag_context, memory_text),
            json_mode=True,
        )
    except LLMError as exc:
        log.warning("Planner LLM call failed (%s); using rule planner", exc.detail)
        return plan_with_rules(question, source, intent), "rules"

    payload = response.json()
    if response.from_demo or not isinstance(payload, dict) or payload.get("use_deterministic_fallback"):
        return plan_with_rules(question, source, intent), "rules"

    payload.setdefault("intent", intent)
    payload["dataset"] = source.name
    payload["source_type"] = "sql" if source.kind == "sql" else "dataframe"
    try:
        plan = QueryPlan.model_validate(payload)
    except ValidationError as exc:
        log.warning("LLM plan failed schema validation (%s); using rule planner", exc)
        return plan_with_rules(question, source, intent), "rules"
    return repair_plan(plan, source), "llm"


def repair_plan(plan: QueryPlan, source: DataSource) -> QueryPlan:
    """Map near-miss column names onto real ones and drop invented ones."""
    dropped: list[str] = []

    def resolve_all(names: list[str]) -> list[str]:
        out = []
        for name in names:
            resolved = source.resolve_column(name)
            if resolved:
                out.append(resolved)
            else:
                dropped.append(name)
        return out

    plan.dimensions = resolve_all(plan.dimensions)
    plan.measures = resolve_all(plan.measures)
    if plan.time_column:
        plan.time_column = source.resolve_column(plan.time_column)
    if plan.sort_by:
        plan.sort_by = source.resolve_column(plan.sort_by)

    kept_filters = []
    for condition in plan.filters:
        resolved = source.resolve_column(condition.column)
        if resolved:
            condition.column = resolved
            kept_filters.append(condition)
        else:
            dropped.append(condition.column)
    plan.filters = kept_filters

    if dropped:
        note = f"Ignored columns that are not in this dataset: {', '.join(sorted(set(dropped)))}."
        plan.assumptions = list(plan.assumptions) + [note]
        log.warning("Plan repair dropped hallucinated columns: %s", sorted(set(dropped)))
    return plan


def validate_plan(plan: QueryPlan, source: DataSource) -> ValidationReport:
    """Hard gate before execution."""
    report = ValidationReport()
    columns = {c.lower() for c in source.columns}

    unknown = [c for c in plan.dimensions + plan.measures if c.lower() not in columns]
    report.add("columns_exist", not unknown, f"unknown columns: {', '.join(unknown)}" if unknown else "")

    if plan.time_column:
        report.add(
            "time_column_exists",
            plan.time_column.lower() in columns,
            f"'{plan.time_column}' is not a column in this dataset",
        )

    for condition in plan.filters:
        report.add(
            f"filter_column:{condition.column}",
            condition.column.lower() in columns,
            f"'{condition.column}' is not a column in this dataset",
        )

    numeric = {m.lower() for m in source.measures}
    if plan.aggregation in {"sum", "avg", "mean", "median", "std"}:
        non_numeric = [m for m in plan.measures if m.lower() not in numeric]
        report.add(
            "measures_numeric",
            not non_numeric,
            f"{', '.join(non_numeric)} cannot be aggregated with {plan.aggregation.upper()}"
            if non_numeric
            else "",
        )

    if plan.intent in ANALYTICAL_INTENTS and not plan.measures and plan.aggregation != "count":
        report.warn("No measure selected; the analysis will count rows.")

    if plan.limit and plan.limit > 1000:
        report.warn("Limit reduced to 1000 rows.")
        plan.limit = 1000

    report.add("plan_shape", True, f"{plan.aggregation.upper()} over {len(plan.dimensions)} dimension(s)")
    if not report.valid:
        log.warning("Plan validation failed: %s", report.errors)
    return report


def plan_query(
    question: str,
    source: DataSource,
    intent: str,
    rag_context: str = "",
    memory: dict | None = None,
    memory_text: str = "",
) -> tuple[QueryPlan, str]:
    """Plan, then validate; on failure retry deterministically once."""
    plan, method = plan_with_llm(question, source, intent, rag_context, memory_text)
    if method == "rules":
        plan = plan_with_rules(question, source, intent, memory)
    report = validate_plan(plan, source)
    if not report.valid:
        log.info("Falling back to the rule planner after validation failure")
        plan = plan_with_rules(question, source, intent, memory)
        method = "rules(repaired)"
        report = validate_plan(plan, source)
        if not report.valid:
            raise PlanValidationError(
                "AInsight could not build a valid analysis for that question against this dataset.",
                "; ".join(report.errors),
            )
    return plan, method


def plan_to_dict(plan: QueryPlan) -> dict[str, Any]:
    return plan.model_dump()
