"""CSV/TSV loading with encoding and delimiter detection."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from app.utils.exceptions import FileProcessingError
from app.utils.logger import get_logger

log = get_logger("files.csv")

ENCODINGS = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]


def detect_encoding(path: Path) -> str:
    raw = Path(path).read_bytes()[:200_000]
    try:
        import chardet  # optional

        guess = chardet.detect(raw)
        if guess and guess.get("encoding") and (guess.get("confidence") or 0) > 0.7:
            return guess["encoding"]
    except Exception:
        pass
    for encoding in ENCODINGS:
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def detect_delimiter(path: Path, encoding: str) -> str:
    with open(path, "r", encoding=encoding, errors="replace") as handle:
        sample = handle.read(16_000)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return "\t" if Path(path).suffix.lower() == ".tsv" else ","


def load_csv(path: str | Path, nrows: int | None = None) -> tuple[pd.DataFrame, dict]:
    path = Path(path)
    if not path.exists():
        raise FileProcessingError("That file is no longer on disk.", f"missing: {path}")
    encoding = detect_encoding(path)
    delimiter = detect_delimiter(path, encoding)
    try:
        df = pd.read_csv(
            path,
            encoding=encoding,
            sep=delimiter,
            nrows=nrows,
            low_memory=False,
            skip_blank_lines=True,
        )
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="latin-1", sep=delimiter, nrows=nrows, low_memory=False)
    except pd.errors.EmptyDataError as exc:
        raise FileProcessingError("That CSV file has no rows.", str(exc)) from exc
    except pd.errors.ParserError as exc:
        raise FileProcessingError(
            "That CSV could not be parsed. Check for inconsistent columns.", str(exc)
        ) from exc

    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if not c.startswith("Unnamed:")] or list(df.columns)]
    if df.empty:
        raise FileProcessingError("That CSV file contains no data rows.")
    meta = {"encoding": encoding, "delimiter": delimiter, "file_type": "csv"}
    log.info("Loaded CSV %s (%s rows, encoding=%s, delim=%r)", path.name, len(df), encoding, delimiter)
    return df, meta
