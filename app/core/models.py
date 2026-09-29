"""Validated structures that flow through the AInsight pipeline.

These models are the contract between the LLM (which may produce anything) and
the deterministic execution engines (which may only run what validates).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.schema import BaseModel, Field, ValidationError, field_validator

# --------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------

INTENTS = [
    "simple_aggregation",
    "filtering",
    "grouping",
    "ranking",
    "comparison",
    "trend_analysis",
    "time_series",
    "percentage_analysis",
    "statistical_analysis",
    "visualization",
    "descriptive_analysis",
    "dataset_question",
    "schema_question",
    "unsupported_request",
    "general_chat",
]

ANALYTICAL_INTENTS = {
    "simple_aggregation",
    "filtering",
    "grouping",
    "ranking",
    "comparison",
    "trend_analysis",
    "time_series",
    "percentage_analysis",
    "statistical_analysis",
    "visualization",
    "descriptive_analysis",
}

AGGREGATIONS = ["sum", "avg", "mean", "count", "count_distinct", "min", "max", "median", "std"]
SQL_AGGREGATIONS = {
    "sum": "SUM",
    "avg": "AVG",
    "mean": "AVG",
    "count": "COUNT",
    "count_distinct": "COUNT(DISTINCT {col})",
    "min": "MIN",
    "max": "MAX",
    "median": "AVG",  # SQLite has no MEDIAN; the planner routes median to pandas
    "std": "AVG",
}
FILTER_OPERATORS = [
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "between",
    "is_null",
    "not_null",
]
CHART_TYPES = ["bar", "horizontal_bar", "line", "pie", "donut", "scatter", "histogram", "table"]
SORT_DIRECTIONS = ["asc", "desc", "none"]


# --------------------------------------------------------------------------
# Plan structures
# --------------------------------------------------------------------------


class Filter(BaseModel):
    """A single validated filter condition."""

    column: str
    operator: str = "="
    value: Any = None

    @field_validator("operator")
    def _check_operator(cls, v):
        v = str(v).strip().lower()
        alias = {"==": "=", "<>": "!=", "eq": "=", "gt": ">", "lt": "<", "like": "contains"}
        v = alias.get(v, v)
        if v not in FILTER_OPERATORS:
            raise ValueError(f"unsupported operator '{v}'")
        return v


class QueryPlan(BaseModel):
    """The structured analysis AInsight intends to run.

    Produced by the planner (LLM or deterministic), validated against the real
    dataset schema before anything executes.
    """

    intent: str = "simple_aggregation"
    dataset: str = ""
    dimensions: List[str] = Field(default_factory=list)
    measures: List[str] = Field(default_factory=list)
    filters: List[Filter] = Field(default_factory=list)
    aggregation: str = "sum"
    sort: str = "desc"
    sort_by: Optional[str] = None
    limit: Optional[int] = None
    time_column: Optional[str] = None
    time_grain: Optional[str] = None  # day | week | month | quarter | year
    visualization: str = "table"
    source_type: str = "dataframe"  # dataframe | sql
    reasoning: str = ""
    assumptions: List[str] = Field(default_factory=list)

    @field_validator("intent")
    def _check_intent(cls, v):
        v = str(v).strip().lower()
        if v not in INTENTS:
            raise ValueError(f"unknown intent '{v}'")
        return v

    @field_validator("aggregation")
    def _check_aggregation(cls, v):
        v = str(v or "sum").strip().lower()
        alias = {"average": "avg", "total": "sum", "distinct_count": "count_distinct"}
        v = alias.get(v, v)
        if v not in AGGREGATIONS:
            raise ValueError(f"unsupported aggregation '{v}'")
        return v

    @field_validator("sort")
    def _check_sort(cls, v):
        v = str(v or "desc").strip().lower()
        alias = {"descending": "desc", "ascending": "asc", "": "none", "none": "none"}
        v = alias.get(v, v)
        if v not in SORT_DIRECTIONS:
            raise ValueError(f"unsupported sort direction '{v}'")
        return v

    @field_validator("visualization")
    def _check_visualization(cls, v):
        v = str(v or "table").strip().lower()
        alias = {"barchart": "bar", "hbar": "horizontal_bar", "doughnut": "donut", "none": "table"}
        v = alias.get(v, v)
        if v not in CHART_TYPES:
            raise ValueError(f"unsupported visualization '{v}'")
        return v

    @field_validator("time_grain")
    def _check_grain(cls, v):
        if v in (None, ""):
            return None
        v = str(v).strip().lower()
        if v not in {"day", "week", "month", "quarter", "year"}:
            raise ValueError(f"unsupported time grain '{v}'")
        return v

    @field_validator("limit")
    def _check_limit(cls, v):
        if v in (None, ""):
            return None
        v = int(v)
        if v <= 0:
            raise ValueError("limit must be positive")
        return min(v, 1000)


class PandasOperation(BaseModel):
    """A deterministic pandas instruction. No Python code is ever executed."""

    operation: str = "aggregate"  # aggregate | groupby | topn | trend | describe | distribution | correlation | preview
    group_by: List[str] = Field(default_factory=list)
    metric: Optional[str] = None
    metrics: List[str] = Field(default_factory=list)
    aggregation: str = "sum"
    filters: List[Filter] = Field(default_factory=list)
    sort: str = "desc"
    limit: Optional[int] = None
    time_column: Optional[str] = None
    time_grain: Optional[str] = None

    @field_validator("operation")
    def _check_operation(cls, v):
        v = str(v or "aggregate").strip().lower()
        allowed = {
            "aggregate",
            "groupby",
            "topn",
            "trend",
            "describe",
            "distribution",
            "correlation",
            "preview",
            "count_rows",
        }
        if v not in allowed:
            raise ValueError(f"unsupported pandas operation '{v}'")
        return v


class ChartSpec(BaseModel):
    """Plotly chart description. Values always come from executed results."""

    chart_type: str = "bar"
    x: Optional[str] = None
    y: Optional[str] = None
    title: str = ""
    x_title: str = ""
    y_title: str = ""
    orientation: str = "v"


class ColumnProfile(BaseModel):
    name: str
    dtype: str = "unknown"
    semantic_type: str = "categorical"  # numeric | categorical | datetime | boolean | text | identifier
    null_count: int = 0
    null_percentage: float = 0.0
    unique_count: int = 0
    sample_values: List[Any] = Field(default_factory=list)
    min: Any = None
    max: Any = None
    mean: Any = None
    description: str = ""


class ExecutionResult(BaseModel):
    """Everything the deterministic engines return about one execution."""

    success: bool = True
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    scalar: Any = None
    sql: Optional[str] = None
    operation: str = ""
    engine: str = "pandas"  # pandas | sql
    execution_ms: float = 0.0
    truncated: bool = False
    message: str = ""


class ValidationReport(BaseModel):
    valid: bool = True
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})
        if not passed:
            self.valid = False
            self.errors.append(f"{name}: {detail}" if detail else name)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


__all__ = [
    "INTENTS",
    "ANALYTICAL_INTENTS",
    "AGGREGATIONS",
    "SQL_AGGREGATIONS",
    "FILTER_OPERATORS",
    "CHART_TYPES",
    "Filter",
    "QueryPlan",
    "PandasOperation",
    "ChartSpec",
    "ColumnProfile",
    "ExecutionResult",
    "ValidationReport",
    "ValidationError",
]
