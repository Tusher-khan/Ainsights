"""Dataset profiling.

Produces the metadata document that drives RAG retrieval, planning, validation
and the dataset profile UI. Profiling is entirely deterministic — no LLM is
involved in deciding what a dataset contains.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from config import Config
from app.core.models import ColumnProfile
from app.utils.helpers import json_safe, slugify
from app.utils.logger import get_logger

log = get_logger("files.profiler")

_ID_HINTS = re.compile(r"(^|_| )(id|code|key|uuid|number|no)$", re.I)
_DATE_HINTS = re.compile(r"(date|time|day|month|year|timestamp|created|updated)", re.I)
_MEASURE_HINTS = re.compile(
    r"(sales|revenue|profit|amount|price|cost|qty|quantity|total|value|discount|margin|count|score|spend|units)",
    re.I,
)


def _semantic_type(series: pd.Series, name: str) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_numeric_dtype(series):
        non_null = series.dropna()
        if _ID_HINTS.search(name) and non_null.nunique() > 0.9 * max(len(non_null), 1):
            return "identifier"
        return "numeric"
    if _ID_HINTS.search(name):
        return "identifier"
    non_null = series.dropna().astype(str)
    if len(non_null) and non_null.str.len().mean() > 60:
        return "text"
    return "categorical"


def coerce_datetimes(df: pd.DataFrame) -> pd.DataFrame:
    """Convert obvious date columns to datetime without corrupting other data."""
    for column in df.columns:
        series = df[column]
        if pd.api.types.is_datetime64_any_dtype(series) or pd.api.types.is_numeric_dtype(series):
            continue
        if not _DATE_HINTS.search(str(column)):
            continue
        sample = series.dropna().astype(str).head(200)
        if sample.empty:
            continue
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
        except (TypeError, ValueError):
            try:
                parsed = pd.to_datetime(sample, errors="coerce")
            except Exception:
                continue
        if parsed.notna().mean() > 0.8:
            try:
                df[column] = pd.to_datetime(series, errors="coerce", format="mixed")
            except (TypeError, ValueError):
                df[column] = pd.to_datetime(series, errors="coerce")
            log.info("Parsed column '%s' as datetime", column)
    return df


def _describe_column(profile: ColumnProfile) -> str:
    """Plain-language description used in RAG metadata documents."""
    kind = {
        "numeric": "numeric measure",
        "categorical": "categorical dimension",
        "datetime": "date/time dimension",
        "boolean": "boolean flag",
        "identifier": "identifier",
        "text": "free text field",
    }[profile.semantic_type]
    bits = [f"Column '{profile.name}' is a {kind} stored as {profile.dtype}."]
    if profile.semantic_type == "numeric" and profile.min is not None:
        bits.append(f"Values range from {profile.min} to {profile.max} with mean {profile.mean}.")
    if profile.semantic_type in {"categorical", "identifier", "boolean"}:
        bits.append(f"It has {profile.unique_count} distinct values.")
    if profile.sample_values:
        preview = ", ".join(str(v) for v in profile.sample_values[:4])
        bits.append(f"Examples: {preview}.")
    if profile.null_percentage:
        bits.append(f"{profile.null_percentage}% of rows are missing this value.")
    return " ".join(bits)


def profile_column(series: pd.Series, name: str) -> ColumnProfile:
    total = len(series)
    nulls = int(series.isna().sum())
    semantic = _semantic_type(series, name)
    non_null = series.dropna()

    sample_values = [
        json_safe(v) for v in non_null.drop_duplicates().head(Config.SAMPLE_VALUES_PER_COLUMN)
    ]
    minimum = maximum = mean = None
    if semantic == "numeric" and not non_null.empty:
        minimum = json_safe(round(float(non_null.min()), 4))
        maximum = json_safe(round(float(non_null.max()), 4))
        mean = json_safe(round(float(non_null.mean()), 4))
    elif semantic == "datetime" and not non_null.empty:
        minimum = str(non_null.min())
        maximum = str(non_null.max())

    profile = ColumnProfile(
        name=str(name),
        dtype=str(series.dtype),
        semantic_type=semantic,
        null_count=nulls,
        null_percentage=round(nulls / total * 100, 2) if total else 0.0,
        unique_count=int(non_null.nunique()),
        sample_values=sample_values,
        min=minimum,
        max=maximum,
        mean=mean,
    )
    profile.description = _describe_column(profile)
    return profile


def suggested_questions(profile: dict) -> list[str]:
    """Generate starter questions from real metadata (not invented columns)."""
    measures = [c["name"] for c in profile["columns"] if c["semantic_type"] == "numeric"]
    dimensions = [
        c["name"]
        for c in profile["columns"]
        if c["semantic_type"] == "categorical" and 1 < c["unique_count"] <= 60
    ]
    dates = [c["name"] for c in profile["columns"] if c["semantic_type"] == "datetime"]

    preferred = [m for m in measures if _MEASURE_HINTS.search(m)] or measures
    questions: list[str] = []
    if preferred:
        questions.append(f"What is the total {preferred[0]}?")
    if preferred and dimensions:
        questions.append(f"Which {dimensions[0]} has the highest {preferred[0]}?")
        questions.append(f"Show {preferred[0]} by {dimensions[0]} as a chart.")
    if preferred and dates:
        questions.append(f"Show monthly {preferred[0]} trends.")
    if preferred and dimensions:
        questions.append(f"Top 10 {dimensions[-1]} by {preferred[0]}.")
    if len(preferred) > 1:
        questions.append(f"Compare {preferred[0]} and {preferred[1]}.")
    if not questions:
        questions = ["How many rows are in this dataset?", "What columns does this dataset have?"]
    return questions[:6]


def build_profile(df: pd.DataFrame, name: str, source_type: str, extra: dict | None = None) -> dict:
    """Full dataset profile: shape, quality, columns, statistics, samples."""
    df = coerce_datetimes(df)
    columns = [profile_column(df[c], str(c)) for c in df.columns]
    duplicate_rows = int(df.duplicated().sum())
    total_cells = int(df.shape[0] * df.shape[1]) or 1
    missing_cells = int(df.isna().sum().sum())

    numeric = [c.name for c in columns if c.semantic_type == "numeric"]
    statistics: dict[str, Any] = {}
    if numeric:
        desc = df[numeric].describe().replace({np.nan: None})
        statistics = {
            col: {k: json_safe(v) for k, v in desc[col].items()} for col in desc.columns
        }

    profile = {
        "name": name,
        "slug": slugify(name),
        "source_type": source_type,
        "row_count": int(df.shape[0]),
        "column_count": int(df.shape[1]),
        "columns": [c.model_dump() for c in columns],
        "column_names": [c.name for c in columns],
        "measures": numeric,
        "dimensions": [c.name for c in columns if c.semantic_type in {"categorical", "boolean"}],
        "date_columns": [c.name for c in columns if c.semantic_type == "datetime"],
        "quality": {
            "duplicate_rows": duplicate_rows,
            "missing_cells": missing_cells,
            "missing_percentage": round(missing_cells / total_cells * 100, 2),
            "complete_columns": sum(1 for c in columns if c.null_count == 0),
            "score": round(
                max(0.0, 100 - (missing_cells / total_cells * 100) - (duplicate_rows / max(len(df), 1) * 10)),
                1,
            ),
        },
        "statistics": statistics,
        "sample_rows": [
            {str(k): json_safe(v) for k, v in row.items()} for row in df.head(10).to_dict("records")
        ],
    }
    profile.update(extra or {})
    profile["suggested_questions"] = suggested_questions(profile)
    log.info("Profiled '%s': %s rows x %s columns", name, profile["row_count"], profile["column_count"])
    return profile
