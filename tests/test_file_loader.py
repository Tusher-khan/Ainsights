"""CSV and Excel loading."""

import pandas as pd
import pytest

from app.files.csv_loader import detect_delimiter, detect_encoding, load_csv
from app.files.excel_loader import list_sheets, load_excel
from app.utils.exceptions import FileProcessingError


def test_load_csv_returns_frame_and_metadata(csv_path):
    frame, meta = load_csv(csv_path)
    assert len(frame) == 12
    assert "Sales" in frame.columns
    assert meta["file_type"] == "csv"
    assert meta["delimiter"] == ","


def test_detects_semicolon_delimiter(tmp_path):
    path = tmp_path / "semi.csv"
    path.write_text("a;b;c\n1;2;3\n4;5;6\n", encoding="utf-8")
    assert detect_delimiter(path, detect_encoding(path)) == ";"
    frame, _ = load_csv(path)
    assert list(frame.columns) == ["a", "b", "c"]


def test_detects_non_utf8_encoding(tmp_path):
    path = tmp_path / "latin.csv"
    path.write_bytes("name,city\nJosé,Málaga\n".encode("cp1252"))
    frame, meta = load_csv(path)
    assert len(frame) == 1
    assert meta["encoding"]


def test_empty_csv_is_rejected(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(FileProcessingError):
        load_csv(path)


def test_excel_sheets_are_listed(excel_path):
    assert list_sheets(excel_path) == ["Orders", "Summary"]


def test_excel_loads_named_sheet(excel_path):
    frame, meta = load_excel(excel_path, "Summary")
    assert len(frame) == 4
    assert meta["sheet_name"] == "Summary"
    assert meta["sheet_count"] == 2


def test_excel_unknown_sheet_is_rejected(excel_path):
    with pytest.raises(FileProcessingError):
        load_excel(excel_path, "DoesNotExist")
