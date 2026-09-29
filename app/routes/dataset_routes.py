"""Dataset management endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify

from app.services import dataset_service
from app.services.database_service import available_tables, schema_payload
from app.utils.exceptions import DatasetNotFoundError

dataset_bp = Blueprint("datasets", __name__, url_prefix="/api")


@dataset_bp.get("/datasets")
def list_datasets():
    return jsonify({"datasets": dataset_service.list_datasets(), "tables": available_tables()})


@dataset_bp.get("/datasets/<dataset_id>")
def get_dataset(dataset_id: str):
    return jsonify(dataset_service.dataset_detail(dataset_id))


@dataset_bp.delete("/datasets/<dataset_id>")
def delete_dataset(dataset_id: str):
    if not dataset_service.delete_dataset(dataset_id):
        raise DatasetNotFoundError()
    return jsonify({"status": "deleted", "id": dataset_id})


@dataset_bp.post("/datasets/<dataset_id>/refresh")
def refresh_dataset(dataset_id: str):
    return jsonify(dataset_service.dataset_detail(dataset_service.refresh_dataset(dataset_id)["id"]))


@dataset_bp.get("/schema/<dataset_id>")
def get_schema(dataset_id: str):
    dataset = dataset_service.get_dataset(dataset_id)
    profile = dataset.get("profile") or {}
    return jsonify(
        {
            "dataset_id": dataset_id,
            "name": dataset["name"],
            "row_count": profile.get("row_count", 0),
            "columns": profile.get("columns", []),
            "measures": profile.get("measures", []),
            "dimensions": profile.get("dimensions", []),
            "date_columns": profile.get("date_columns", []),
        }
    )


@dataset_bp.get("/database/schema")
def database_schema():
    return jsonify({"tables": schema_payload()})
