"""Store, repository and read-only enforcement."""

import sqlite3

import pytest

from app.database import repository
from app.database.connection import analytics_connection, analytics_writer
from app.database.schema_inspector import describe_table, list_tables, schema_summary


def test_write_is_denied_on_the_analytics_connection():
    with analytics_connection() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("CREATE TABLE should_not_exist (x INTEGER)")


def test_select_is_allowed_on_the_analytics_connection():
    with analytics_connection() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_dataset_crud_round_trip():
    created = repository.create_dataset(
        {"name": "Temp", "slug": "temp", "source_type": "csv", "row_count": 3, "column_count": 2,
         "profile": {"row_count": 3, "column_names": ["a", "b"]}}
    )
    assert repository.get_dataset(created["id"])["name"] == "Temp"
    assert repository.find_dataset_by_name("temp")["id"] == created["id"]

    repository.update_dataset(created["id"], status="stale")
    assert repository.get_dataset(created["id"])["status"] == "stale"

    assert repository.delete_dataset(created["id"]) is True
    assert repository.get_dataset(created["id"]) is None


def test_history_and_feedback_round_trip():
    history_id = repository.add_history(
        {"question": "test question", "dataset_name": "Orders", "intent": "simple_aggregation",
         "status": "success", "row_count": 1, "total_ms": 12.5, "answer": "42",
         "result": {"columns": ["x"], "rows": [{"x": 42}]}}
    )
    entry = repository.get_history_entry(history_id)
    assert entry["question"] == "test question"
    assert entry["result"]["rows"] == [{"x": 42}]

    repository.add_feedback(history_id, "helpful", "clear answer")
    assert repository.feedback_summary()["helpful"] >= 1

    assert repository.delete_history(history_id) == 1


def test_conversation_messages_round_trip():
    conversation_id = repository.ensure_conversation(None)
    repository.add_message(conversation_id, "user", "hello")
    repository.add_message(conversation_id, "assistant", "hi", {"dataset_id": "ds_1"})
    messages = repository.get_messages(conversation_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["payload"]["dataset_id"] == "ds_1"


def test_schema_inspector_sees_store_tables():
    tables = list_tables(include_internal=True)
    assert "datasets" in tables
    info = describe_table("datasets", sample_rows=1)
    assert any(c["name"] == "id" for c in info["columns"])
    assert isinstance(schema_summary(["datasets"]), str)


def test_metrics_summary_shape():
    metrics = repository.metrics_summary()
    for key in ("datasets", "queries", "successful_queries", "feedback"):
        assert key in metrics


def test_analytics_writer_is_separate_from_readonly():
    conn = analytics_writer()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS writer_probe (x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    with analytics_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM writer_probe").fetchone()[0] == 0
