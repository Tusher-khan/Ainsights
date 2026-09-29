"""Data-access layer for the application store.

Routes and services never write SQL against the store directly; they call these
functions, which keeps the storage engine swappable.
"""

from __future__ import annotations

from typing import Any

from app.database.connection import app_store
from app.utils.helpers import dumps, loads, new_id, utcnow


# --------------------------------------------------------------------------
# Datasets
# --------------------------------------------------------------------------


def create_dataset(record: dict) -> dict:
    now = utcnow()
    row = {
        "id": record.get("id") or new_id("ds"),
        "name": record["name"],
        "slug": record["slug"],
        "source_type": record["source_type"],
        "file_path": record.get("file_path"),
        "sheet_name": record.get("sheet_name"),
        "table_name": record.get("table_name"),
        "row_count": int(record.get("row_count", 0)),
        "column_count": int(record.get("column_count", 0)),
        "size_bytes": int(record.get("size_bytes", 0)),
        "status": record.get("status", "ready"),
        "profile_json": dumps(record.get("profile", {})),
        "created_at": now,
        "updated_at": now,
    }
    with app_store() as conn:
        conn.execute(
            """INSERT INTO datasets (id,name,slug,source_type,file_path,sheet_name,table_name,
                 row_count,column_count,size_bytes,status,profile_json,created_at,updated_at)
               VALUES (:id,:name,:slug,:source_type,:file_path,:sheet_name,:table_name,
                 :row_count,:column_count,:size_bytes,:status,:profile_json,:created_at,:updated_at)""",
            row,
        )
    return get_dataset(row["id"])


def update_dataset(dataset_id: str, **fields: Any) -> dict | None:
    if not fields:
        return get_dataset(dataset_id)
    if "profile" in fields:
        fields["profile_json"] = dumps(fields.pop("profile"))
    fields["updated_at"] = utcnow()
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = dataset_id
    with app_store() as conn:
        conn.execute(f"UPDATE datasets SET {assignments} WHERE id = :id", fields)
    return get_dataset(dataset_id)


def _dataset_row(row) -> dict:
    data = dict(row)
    data["profile"] = loads(data.pop("profile_json", None), {}) or {}
    return data


def get_dataset(dataset_id: str) -> dict | None:
    with app_store() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    return _dataset_row(row) if row else None


def find_dataset_by_name(name: str) -> dict | None:
    with app_store() as conn:
        row = conn.execute(
            "SELECT * FROM datasets WHERE lower(name) = lower(?) OR slug = lower(?) LIMIT 1",
            (name, name),
        ).fetchone()
    return _dataset_row(row) if row else None


def list_datasets() -> list[dict]:
    with app_store() as conn:
        rows = conn.execute("SELECT * FROM datasets ORDER BY created_at DESC").fetchall()
    return [_dataset_row(r) for r in rows]


def delete_dataset(dataset_id: str) -> bool:
    with app_store() as conn:
        cur = conn.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
    return cur.rowcount > 0


# --------------------------------------------------------------------------
# Conversations and messages
# --------------------------------------------------------------------------


def create_conversation(dataset_id: str | None = None, title: str = "New analysis") -> dict:
    now = utcnow()
    cid = new_id("conv")
    with app_store() as conn:
        conn.execute(
            "INSERT INTO conversations (id,title,dataset_id,created_at,updated_at) VALUES (?,?,?,?,?)",
            (cid, title, dataset_id, now, now),
        )
    return {"id": cid, "title": title, "dataset_id": dataset_id, "created_at": now}


def ensure_conversation(conversation_id: str | None, dataset_id: str | None = None) -> str:
    if conversation_id:
        with app_store() as conn:
            row = conn.execute(
                "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        if row:
            return conversation_id
        now = utcnow()
        with app_store() as conn:
            conn.execute(
                "INSERT INTO conversations (id,title,dataset_id,created_at,updated_at) VALUES (?,?,?,?,?)",
                (conversation_id, "New analysis", dataset_id, now, now),
            )
        return conversation_id
    return create_conversation(dataset_id)["id"]


def add_message(conversation_id: str, role: str, content: str, payload: dict | None = None) -> str:
    mid = new_id("msg")
    now = utcnow()
    with app_store() as conn:
        conn.execute(
            "INSERT INTO messages (id,conversation_id,role,content,payload_json,created_at) VALUES (?,?,?,?,?,?)",
            (mid, conversation_id, role, content, dumps(payload or {}), now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ?, dataset_id = COALESCE(?, dataset_id) WHERE id = ?",
            (now, (payload or {}).get("dataset_id"), conversation_id),
        )
    return mid


def get_messages(conversation_id: str, limit: int = 20) -> list[dict]:
    with app_store() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
    messages = []
    for row in reversed(rows):
        item = dict(row)
        item["payload"] = loads(item.pop("payload_json", None), {}) or {}
        messages.append(item)
    return messages


def clear_conversation(conversation_id: str) -> None:
    with app_store() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))


# --------------------------------------------------------------------------
# Query history
# --------------------------------------------------------------------------


def add_history(entry: dict) -> str:
    hid = entry.get("id") or new_id("q")
    row = {
        "id": hid,
        "conversation_id": entry.get("conversation_id"),
        "question": entry.get("question", ""),
        "dataset_id": entry.get("dataset_id"),
        "dataset_name": entry.get("dataset_name"),
        "intent": entry.get("intent"),
        "engine": entry.get("engine"),
        "sql": entry.get("sql"),
        "plan_json": dumps(entry.get("plan") or {}),
        "status": entry.get("status", "success"),
        "error": entry.get("error"),
        "row_count": int(entry.get("row_count") or 0),
        "execution_ms": float(entry.get("execution_ms") or 0),
        "total_ms": float(entry.get("total_ms") or 0),
        "answer": entry.get("answer"),
        "result_json": dumps(entry.get("result") or {}),
        "created_at": utcnow(),
    }
    with app_store() as conn:
        conn.execute(
            """INSERT INTO query_history (id,conversation_id,question,dataset_id,dataset_name,intent,
                 engine,sql,plan_json,status,error,row_count,execution_ms,total_ms,answer,result_json,created_at)
               VALUES (:id,:conversation_id,:question,:dataset_id,:dataset_name,:intent,:engine,:sql,
                 :plan_json,:status,:error,:row_count,:execution_ms,:total_ms,:answer,:result_json,:created_at)""",
            row,
        )
    return hid


def list_history(limit: int = 100) -> list[dict]:
    with app_store() as conn:
        rows = conn.execute(
            "SELECT * FROM query_history ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["plan"] = loads(item.pop("plan_json", None), {})
        item.pop("result_json", None)
        out.append(item)
    return out


def get_history_entry(history_id: str) -> dict | None:
    with app_store() as conn:
        row = conn.execute("SELECT * FROM query_history WHERE id = ?", (history_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["plan"] = loads(item.pop("plan_json", None), {})
    item["result"] = loads(item.pop("result_json", None), {})
    return item


def delete_history(history_id: str | None = None) -> int:
    with app_store() as conn:
        if history_id:
            cur = conn.execute("DELETE FROM query_history WHERE id = ?", (history_id,))
        else:
            cur = conn.execute("DELETE FROM query_history")
    return cur.rowcount


def add_feedback(history_id: str | None, rating: str, comment: str = "") -> str:
    fid = new_id("fb")
    with app_store() as conn:
        conn.execute(
            "INSERT INTO feedback (id,history_id,rating,comment,created_at) VALUES (?,?,?,?,?)",
            (fid, history_id, rating, comment[:1000], utcnow()),
        )
    return fid


def feedback_summary() -> dict:
    with app_store() as conn:
        rows = conn.execute("SELECT rating, COUNT(*) c FROM feedback GROUP BY rating").fetchall()
    counts = {r["rating"]: r["c"] for r in rows}
    helpful = counts.get("helpful", 0)
    total = sum(counts.values())
    return {
        "helpful": helpful,
        "not_helpful": counts.get("not_helpful", 0),
        "total": total,
        "helpful_rate": round(helpful / total * 100, 1) if total else None,
    }


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def metrics_summary() -> dict:
    with app_store() as conn:
        datasets = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(row_count),0) rows FROM datasets"
        ).fetchone()
        hist = conn.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) ok,
                      AVG(total_ms) avg_ms
               FROM query_history"""
        ).fetchone()
        by_intent = conn.execute(
            "SELECT intent, COUNT(*) c FROM query_history GROUP BY intent ORDER BY c DESC"
        ).fetchall()
    total = hist["total"] or 0
    ok = hist["ok"] or 0
    return {
        "datasets": datasets["c"] or 0,
        "total_rows": datasets["rows"] or 0,
        "queries": total,
        "successful_queries": ok,
        "failed_queries": total - ok,
        "success_rate": round(ok / total * 100, 1) if total else None,
        "avg_query_ms": round(hist["avg_ms"], 1) if hist["avg_ms"] else None,
        "intents": [{"intent": r["intent"] or "unknown", "count": r["c"]} for r in by_intent],
        "feedback": feedback_summary(),
    }
