"""The AInsight analysis workflow.

    START → classify_intent → retrieve_metadata → plan_query → validate_plan
          → route_source → generate_query → validate_query → execute_query
          → validate_result → generate_visualization → generate_response
          → update_memory → END

Each stage is a pure function over a shared `AnalysisState`. When LangGraph is
installed the same node functions are wired into a real `StateGraph`; otherwise
an equivalent sequential runner with the same conditional edges and retry
semantics executes them. `workflow_backend()` reports which is active.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from config import Config
from app.agents import pandas_agent, sql_agent
from app.agents.validation_agent import EMPTY_MESSAGE, validate_result
from app.agents.visualization_agent import build_figure
from app.ai import query_planner, response_generator
from app.ai.intent_classifier import classify
from app.core.datasource import DataSource
from app.core.models import ExecutionResult, QueryPlan, ValidationReport
from app.rag import retriever
from app.utils.exceptions import AInsightError, UnsupportedRequestError
from app.utils.helpers import Timer
from app.utils.logger import get_logger

log = get_logger("ai.orchestrator")

try:  # pragma: no cover - optional
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover
    LANGGRAPH_AVAILABLE = False


def workflow_backend() -> str:
    return "langgraph" if LANGGRAPH_AVAILABLE else "builtin-sequential"


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


@dataclass
class AnalysisState:
    question: str
    source: DataSource
    conversation_id: str = ""
    memory: dict = field(default_factory=dict)
    memory_text: str = ""

    intent: str = ""
    intent_confidence: float = 0.0
    intent_method: str = ""
    rag_hits: list = field(default_factory=list)
    rag_context: str = ""
    plan: QueryPlan | None = None
    plan_method: str = ""
    plan_report: ValidationReport | None = None
    engine: str = "pandas"
    result: ExecutionResult | None = None
    query_report: ValidationReport | None = None
    result_report: ValidationReport | None = None
    visualization: dict | None = None
    answer: str = ""
    insight: str = ""
    insight_method: str = ""
    sql: str | None = None
    retries: int = 0
    stages: list = field(default_factory=list)
    error: str | None = None
    terminal: bool = False

    def stage(self, name: str, status: str = "done", detail: str = "", ms: float = 0.0) -> None:
        self.stages.append({"stage": name, "status": status, "detail": detail, "ms": round(ms, 2)})


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


def node_classify_intent(state: AnalysisState) -> AnalysisState:
    with Timer() as timer:
        result = classify(state.question)
    state.intent = result.intent
    state.intent_confidence = result.confidence
    state.intent_method = result.method
    state.stage("classify_intent", detail=f"{result.intent} ({result.method})", ms=timer.ms)

    if result.intent == "general_chat":
        state.answer = "Ask a question about your data and AInsight will analyse it."
        state.insight = (
            "Pick a dataset, then ask something like “what are my total sales?” or "
            "“which category has the highest profit?”."
        )
        state.terminal = True
    elif result.intent == "unsupported_request":
        state.answer = "That question cannot be answered from the available data."
        state.insight = (
            "AInsight only reports what is in your datasets. Try asking about the columns "
            "this dataset actually contains."
        )
        state.terminal = True
    return state


def node_retrieve_metadata(state: AnalysisState) -> AnalysisState:
    with Timer() as timer:
        hits = retriever.retrieve(state.question, dataset_id=state.source.id)
        state.rag_hits = hits
        state.rag_context = retriever.context_block(hits)
    state.stage("retrieve_metadata", detail=f"{len(hits)} metadata documents", ms=timer.ms)
    return state


def node_plan_query(state: AnalysisState) -> AnalysisState:
    with Timer() as timer:
        plan, method = query_planner.plan_query(
            state.question, state.source, state.intent,
            rag_context=state.rag_context, memory=state.memory, memory_text=state.memory_text,
        )
    state.plan = plan
    state.plan_method = method
    state.stage("plan_query", detail=f"{plan.aggregation.upper()} via {method}", ms=timer.ms)
    return state


def node_validate_plan(state: AnalysisState) -> AnalysisState:
    with Timer() as timer:
        report = query_planner.validate_plan(state.plan, state.source)
    state.plan_report = report
    state.stage(
        "validate_plan",
        status="done" if report.valid else "failed",
        detail="plan verified against the dataset schema" if report.valid else "; ".join(report.errors),
        ms=timer.ms,
    )
    if not report.valid:
        raise UnsupportedRequestError(
            "AInsight could not build a valid analysis for that question against this dataset.",
            "; ".join(report.errors),
        )
    return state


def node_route_source(state: AnalysisState) -> AnalysisState:
    state.engine = "sql" if state.source.kind == "sql" else "pandas"
    state.stage(
        "route_source",
        detail=f"{state.source.name} → {'SQL agent' if state.engine == 'sql' else 'pandas analytics agent'}",
    )
    return state


def node_execute(state: AnalysisState) -> AnalysisState:
    """generate_query + validate_query + execute_query.

    Both agents keep generation, validation and execution together because a
    rejected query must be regenerated with the validator's feedback.
    """
    with Timer() as timer:
        if state.engine == "sql":
            result, report, info = sql_agent.run(state.question, state.plan, state.source)
            state.query_report = report
            state.retries = info["retries"]
            state.sql = result.sql
            state.stage("generate_query", detail=f"SQL via {info['method']}")
            state.stage("validate_query", detail=f"{len(report.checks)} security checks passed")
        else:
            result, operation = pandas_agent.execute(state.plan, state.source)
            state.query_report = ValidationReport()
            state.query_report.add("operation_whitelisted", True, f"{operation.operation} is a supported operation")
            state.query_report.add("no_dynamic_code", True, "no eval/exec used")
            state.stage("generate_query", detail=f"pandas operation: {operation.operation}")
            state.stage("validate_query", detail="structured operation validated")
    state.result = result
    state.stage("execute_query", detail=f"{result.row_count} rows in {result.execution_ms}ms", ms=timer.ms)
    return state


def node_validate_result(state: AnalysisState) -> AnalysisState:
    with Timer() as timer:
        report = validate_result(state.result, state.plan, state.source)
    state.result_report = report
    state.stage(
        "validate_result",
        status="done" if report.valid else "failed",
        detail="result verified" if report.valid else "; ".join(report.errors),
        ms=timer.ms,
    )
    return state


def node_generate_visualization(state: AnalysisState) -> AnalysisState:
    with Timer() as timer:
        if state.result.row_count:
            state.visualization = build_figure(state.plan, state.result)
    state.stage(
        "generate_visualization",
        detail=(state.visualization["spec"]["chart_type"] if state.visualization else "table view"),
        ms=timer.ms,
    )
    return state


def node_generate_response(state: AnalysisState) -> AnalysisState:
    with Timer() as timer:
        state.answer = response_generator.answer_text(state.result, state.plan, state.source)
        insight, method = response_generator.generate_insight(
            state.question, state.result, state.plan, state.source
        )
        state.insight = insight
        state.insight_method = method
    state.stage("generate_response", detail=f"insight via {method}", ms=timer.ms)
    return state


def node_update_memory(state: AnalysisState) -> AnalysisState:
    from app.services.memory_service import remember_turn

    if state.conversation_id:
        remember_turn(
            state.conversation_id,
            question=state.question,
            plan=state.plan.model_dump() if state.plan else {},
            dataset_id=state.source.id,
            answer=state.answer,
        )
    state.stage("update_memory", detail="conversation context saved")
    return state


NODES: list[tuple[str, Callable[[AnalysisState], AnalysisState]]] = [
    ("classify_intent", node_classify_intent),
    ("retrieve_metadata", node_retrieve_metadata),
    ("plan_query", node_plan_query),
    ("validate_plan", node_validate_plan),
    ("route_source", node_route_source),
    ("execute_query", node_execute),
    ("validate_result", node_validate_result),
    ("generate_visualization", node_generate_visualization),
    ("generate_response", node_generate_response),
    ("update_memory", node_update_memory),
]


# --------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------


def _run_sequential(state: AnalysisState) -> AnalysisState:
    """Built-in runner with the same edges and short-circuits as the graph."""
    for name, node in NODES:
        if state.terminal and name not in {"update_memory"}:
            continue
        state = node(state)
    return state


def _build_langgraph():  # pragma: no cover - optional dependency
    graph = StateGraph(dict)

    def wrap(fn):
        def runner(payload: dict) -> dict:
            state: AnalysisState = payload["state"]
            if state.terminal:
                return payload
            payload["state"] = fn(state)
            return payload

        return runner

    for name, node in NODES:
        graph.add_node(name, wrap(node))
    graph.add_edge(START, NODES[0][0])
    for (current, _), (nxt, _) in zip(NODES, NODES[1:]):
        graph.add_edge(current, nxt)
    graph.add_edge(NODES[-1][0], END)
    return graph.compile()


_compiled = None


def run_analysis(
    question: str,
    source: DataSource,
    conversation_id: str = "",
    memory: dict | None = None,
    memory_text: str = "",
) -> AnalysisState:
    """Execute the full workflow and return the final state."""
    global _compiled
    state = AnalysisState(
        question=question,
        source=source,
        conversation_id=conversation_id,
        memory=memory or {},
        memory_text=memory_text,
    )
    log.info("Analysis start | dataset=%s | question=%s", source.name, question[:120])

    try:
        if LANGGRAPH_AVAILABLE:  # pragma: no cover
            if _compiled is None:
                _compiled = _build_langgraph()
            output = _compiled.invoke({"state": state})
            state = output["state"]
        else:
            state = _run_sequential(state)
    except AInsightError as exc:
        state.error = exc.user_message
        state.answer = exc.user_message
        state.stage("error", status="failed", detail=exc.detail)
        log.warning("Analysis failed: %s", exc.detail)
        raise
    log.info(
        "Analysis complete | intent=%s | engine=%s | rows=%s",
        state.intent, state.engine, state.result.row_count if state.result else 0,
    )
    return state


def state_to_response(state: AnalysisState) -> dict[str, Any]:
    """Serialise the state into the API/chat payload."""
    result = state.result
    plan = state.plan
    payload: dict[str, Any] = {
        "answer": state.answer,
        "insight": state.insight,
        "intent": state.intent,
        "intent_confidence": round(state.intent_confidence, 2),
        "terminal": state.terminal,
        "stages": state.stages,
        "workflow": workflow_backend(),
        "methods": {
            "intent": state.intent_method,
            "plan": state.plan_method,
            "insight": state.insight_method,
        },
    }
    if plan:
        payload["plan"] = plan.model_dump()
        payload["assumptions"] = plan.assumptions
    if result:
        payload["result"] = {
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "scalar": result.scalar,
            "truncated": result.truncated,
            "execution_ms": result.execution_ms,
            "engine": result.engine,
        }
        payload["headline"] = response_generator.headline(result, plan) if plan else None
        payload["source"] = {
            "dataset": state.source.name,
            "dataset_id": state.source.id,
            "kind": state.source.kind,
            "sheet": state.source.sheet_name,
            "table": state.source.table_name,
            "columns_used": sorted(
                {*(plan.dimensions if plan else []), *(plan.measures if plan else [])}
            ),
            "operation": result.operation,
            "execution_ms": result.execution_ms,
            "row_count": result.row_count,
        }
        payload["sql"] = state.sql
        payload["empty"] = result.row_count == 0
        if result.row_count == 0:
            payload["answer"] = EMPTY_MESSAGE
    if state.visualization:
        payload["visualization"] = state.visualization
    payload["validation"] = {
        "plan": state.plan_report.model_dump() if state.plan_report else None,
        "query": state.query_report.model_dump() if state.query_report else None,
        "result": state.result_report.model_dump() if state.result_report else None,
        "verified": bool(
            state.result_report and state.result_report.valid and state.result and state.result.row_count
        ),
    }
    payload["retries"] = state.retries
    payload["demo_mode"] = Config.DEMO_MODE
    return payload
