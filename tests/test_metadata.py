"""Dataset profiling and metadata extraction."""

from app.files.file_profiler import build_profile, suggested_questions


def test_profile_reports_shape(profile):
    assert profile["row_count"] == 12
    assert profile["column_count"] == 7


def test_semantic_types_are_detected(profile):
    types = {c["name"]: c["semantic_type"] for c in profile["columns"]}
    assert types["Sales"] == "numeric"
    assert types["Region"] == "categorical"
    assert types["Order Date"] == "datetime"
    assert types["Order ID"] == "identifier"


def test_measures_and_dimensions_are_separated(profile):
    assert "Sales" in profile["measures"]
    assert "Profit" in profile["measures"]
    assert "Region" in profile["dimensions"]
    assert "Order Date" in profile["date_columns"]


def test_quality_metrics_present(profile):
    quality = profile["quality"]
    assert quality["duplicate_rows"] == 0
    assert quality["missing_percentage"] == 0.0
    assert 0 <= quality["score"] <= 100


def test_column_descriptions_mention_the_column(profile):
    for column in profile["columns"]:
        assert column["name"] in column["description"]


def test_suggested_questions_use_real_columns(profile):
    questions = suggested_questions(profile)
    assert questions
    names = set(profile["column_names"])
    assert any(any(name in q for name in names) for q in questions)


def test_missing_values_are_counted(sample_frame):
    frame = sample_frame.copy()
    frame.loc[0, "Sales"] = None
    profile = build_profile(frame, "WithGaps", "csv")
    sales = next(c for c in profile["columns"] if c["name"] == "Sales")
    assert sales["null_count"] == 1
    assert profile["quality"]["missing_cells"] == 1
