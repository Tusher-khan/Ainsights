"""Chart selection and figure construction."""

from app.agents.visualization_agent import build_figure, choose_chart
from app.core.models import ExecutionResult, QueryPlan


def result(columns, rows):
    return ExecutionResult(columns=columns, rows=rows, row_count=len(rows))


GROUPED = result(["Region", "total_Sales"],
                 [{"Region": "West", "total_Sales": 600.0}, {"Region": "North", "total_Sales": 400.0}])


def test_ranking_uses_horizontal_bar():
    plan = QueryPlan(intent="ranking", visualization="horizontal_bar")
    figure = build_figure(plan, GROUPED)
    assert figure["spec"]["chart_type"] == "horizontal_bar"
    assert figure["figure"]["data"][0]["orientation"] == "h"


def test_time_series_uses_line():
    rows = [{"period": f"2024-0{i}", "total_Sales": i * 100} for i in range(1, 5)]
    plan = QueryPlan(intent="time_series", visualization="line", time_column="Order Date", time_grain="month")
    figure = build_figure(plan, result(["period", "total_Sales"], rows))
    assert figure["figure"]["data"][0]["type"] == "scatter"
    assert figure["figure"]["data"][0]["mode"] == "lines+markers"


def test_chart_values_come_from_the_result_only():
    figure = build_figure(QueryPlan(intent="grouping", visualization="bar"), GROUPED)
    assert figure["figure"]["data"][0]["y"] == [600.0, 400.0]
    assert figure["figure"]["data"][0]["x"] == ["West", "North"]


def test_pie_downgrades_when_there_are_too_many_slices():
    rows = [{"Product": f"P{i}", "total_Sales": i} for i in range(20)]
    assert choose_chart(QueryPlan(visualization="pie"), result(["Product", "total_Sales"], rows)) == "bar"


def test_single_value_result_gets_no_chart():
    single = result(["total_Sales"], [{"total_Sales": 1000}])
    assert build_figure(QueryPlan(intent="simple_aggregation", visualization="bar"), single) is None


def test_empty_result_gets_no_chart():
    assert build_figure(QueryPlan(visualization="bar"), result(["a", "b"], [])) is None


def test_chart_point_count_is_capped():
    rows = [{"Item": f"I{i}", "total": i} for i in range(1000)]
    figure = build_figure(QueryPlan(intent="grouping", visualization="bar"), result(["Item", "total"], rows))
    assert figure["point_count"] <= 300
