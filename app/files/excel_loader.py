"""Excel loading with multi-sheet support."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.utils.exceptions import FileProcessingError
from app.utils.logger import get_logger

log = get_logger("files.excel")


def list_sheets(path: str | Path) -> list[str]:
    try:
        with pd.ExcelFile(path) as workbook:
            return [str(s) for s in workbook.sheet_names]
    except Exception as exc:
        raise FileProcessingError("That workbook could not be opened.", str(exc)) from exc


def load_excel(path: str | Path, sheet_name: str | None = None) -> tuple[pd.DataFrame, dict]:
    path = Path(path)
    if not path.exists():
        raise FileProcessingError("That workbook is no longer on disk.", f"missing: {path}")
    sheets = list_sheets(path)
    if not sheets:
        raise FileProcessingError("That workbook has no sheets.")
    target = sheet_name or sheets[0]
    if target not in sheets:
        raise FileProcessingError(
            f"Sheet '{target}' is not in this workbook. Available: {', '.join(sheets)}"
        )
    try:
        df = pd.read_excel(path, sheet_name=target)
    except Exception as exc:
        raise FileProcessingError("That sheet could not be read.", str(exc)) from exc

    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all").dropna(axis=1, how="all")
    if df.empty:
        raise FileProcessingError(f"Sheet '{target}' contains no data.")
    meta = {"file_type": "excel", "sheet_name": target, "sheets": sheets, "sheet_count": len(sheets)}
    log.info("Loaded Excel %s[%s] (%s rows)", path.name, target, len(df))
    return df, meta


def load_all_sheets(path: str | Path) -> dict[str, pd.DataFrame]:
    return {name: load_excel(path, name)[0] for name in list_sheets(path)}
