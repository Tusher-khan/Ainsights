"""Small shared helpers: ids, JSON-safety, formatting, timing."""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

SLUG_RE = re.compile(r"[^a-z0-9]+")


def new_id(prefix: str = "") -> str:
    token = uuid.uuid4().hex[:12]
    return f"{prefix}_{token}" if prefix else token


def slugify(value: str, fallback: str = "dataset") -> str:
    slug = SLUG_RE.sub("_", str(value).strip().lower()).strip("_")
    return slug or fallback


def utcnow() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class Timer:
    """Context manager returning elapsed milliseconds."""

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self.ms = round((time.perf_counter() - self._start) * 1000, 2)


def json_safe(value: Any) -> Any:
    """Convert pandas/numpy values into JSON-serialisable Python types."""
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return str(value)
    if isinstance(value, (np.ndarray,)):
        return [json_safe(v) for v in value.tolist()]
    if value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def dataframe_to_records(df: pd.DataFrame, limit: int | None = None) -> list[dict]:
    frame = df.head(limit) if limit else df
    return [{str(k): json_safe(v) for k, v in row.items()} for row in frame.to_dict("records")]


def dumps(value: Any) -> str:
    return json.dumps(json_safe(value), ensure_ascii=False)


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def human_number(value: Any) -> str:
    """Format a number the way a business report would."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number) or math.isinf(number):
        return "—"
    sign = "-" if number < 0 else ""
    magnitude = abs(number)
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if magnitude >= threshold:
            return f"{sign}{magnitude / threshold:.2f}{suffix}"
    if magnitude == int(magnitude):
        return f"{sign}{int(magnitude):,}"
    return f"{sign}{magnitude:,.2f}"


def truncate(text: str, length: int = 240) -> str:
    text = str(text)
    return text if len(text) <= length else text[: length - 1] + "…"


def extract_json(text: str) -> dict | list | None:
    """Pull the first JSON object/array out of an LLM response."""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except ValueError:
                continue
    return None
