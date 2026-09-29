"""Upload endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.file_service import process_upload
from app.utils.exceptions import FileValidationError
from app.utils.logger import get_logger

upload_bp = Blueprint("upload", __name__, url_prefix="/api")
log = get_logger("routes.upload")


@upload_bp.post("/upload")
def post_upload():
    if "file" not in request.files:
        raise FileValidationError("Choose a CSV or Excel file to upload.")
    file_storage = request.files["file"]
    if not file_storage or not file_storage.filename:
        raise FileValidationError("Choose a CSV or Excel file to upload.")

    dataset = process_upload(
        file_storage,
        display_name=(request.form.get("name") or "").strip() or None,
        sheet_name=(request.form.get("sheet") or "").strip() or None,
    )
    profile = dataset.get("profile") or {}
    return (
        jsonify(
            {
                "status": "ready",
                "dataset": {
                    "id": dataset["id"],
                    "name": dataset["name"],
                    "source_type": dataset["source_type"],
                    "row_count": dataset["row_count"],
                    "column_count": dataset["column_count"],
                    "size_bytes": dataset["size_bytes"],
                    "sheet_name": dataset.get("sheet_name"),
                },
                "profile": {
                    "columns": profile.get("columns", []),
                    "quality": profile.get("quality", {}),
                    "measures": profile.get("measures", []),
                    "dimensions": profile.get("dimensions", []),
                    "date_columns": profile.get("date_columns", []),
                    "sample_rows": profile.get("sample_rows", [])[:5],
                    "suggested_questions": profile.get("suggested_questions", []),
                    "file_meta": profile.get("file_meta", {}),
                },
            }
        ),
        201,
    )
