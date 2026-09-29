"""Dataset lifecycle and routing: registration, listing, deletion, selection."""

from __future__ import annotations

from app.core.datasource import DataSource, from_dataset_record, from_sql_table
from app.database import repository
from app.database.schema_inspector import list_tables
from app.files.dataframe_manager import frame_manager
from app.files.file_profiler import suggested_questions
from app.rag import metadata_retriever, retriever
from app.utils.exceptions import DatasetNotFoundError
from app.utils.logger import get_logger

log = get_logger("services.dataset")


def register_dataset(record: dict, profile: dict) -> dict:
    """Store a dataset and index its metadata for retrieval."""
    payload = dict(record)
    payload["profile"] = profile
    payload["row_count"] = profile.get("row_count", 0)
    payload["column_count"] = profile.get("column_count", 0)
    dataset = repository.create_dataset(payload)
    metadata_retriever.index_dataset(dataset["id"], profile)
    log.info("Registered dataset %s (%s)", dataset["name"], dataset["id"])
    return dataset


def list_datasets() -> list[dict]:
    datasets = repository.list_datasets()
    for dataset in datasets:
        profile = dataset.get("profile") or {}
        dataset["summary"] = {
            "measures": profile.get("measures", []),
            "dimensions": profile.get("dimensions", [])[:6],
            "quality_score": (profile.get("quality") or {}).get("score"),
        }
        dataset.pop("profile", None)
    return datasets


def get_dataset(dataset_id: str) -> dict:
    dataset = repository.get_dataset(dataset_id)
    if not dataset:
        raise DatasetNotFoundError()
    return dataset


def dataset_detail(dataset_id: str) -> dict:
    dataset = get_dataset(dataset_id)
    profile = dataset.get("profile") or {}
    dataset["suggested_questions"] = profile.get("suggested_questions") or suggested_questions(profile)
    return dataset


def delete_dataset(dataset_id: str) -> bool:
    dataset = repository.get_dataset(dataset_id)
    if not dataset:
        return False
    frame_manager.invalidate(dataset_id)
    metadata_retriever.remove_dataset(dataset_id)
    deleted = repository.delete_dataset(dataset_id)
    log.info("Deleted dataset %s", dataset_id)
    return deleted


def refresh_dataset(dataset_id: str) -> dict:
    """Re-profile a file from disk (it may have changed) and re-index it."""
    from app.services.file_service import profile_existing_file

    dataset = get_dataset(dataset_id)
    frame_manager.invalidate(dataset_id)
    profile = profile_existing_file(dataset)
    repository.update_dataset(
        dataset_id,
        profile=profile,
        row_count=profile.get("row_count", 0),
        column_count=profile.get("column_count", 0),
        status="ready",
    )
    metadata_retriever.index_dataset(dataset_id, profile)
    return get_dataset(dataset_id)


def get_source(dataset_id: str) -> DataSource:
    return from_dataset_record(get_dataset(dataset_id))


def select_source(question: str, dataset_id: str | None = None, conversation_id: str | None = None) -> tuple[DataSource, str]:
    """Choose the data source: explicit selection, conversation memory, then RAG."""
    if dataset_id:
        return get_source(dataset_id), "selected by you"

    if conversation_id:
        from app.services.memory_service import active_dataset

        remembered = active_dataset(conversation_id)
        if remembered and repository.get_dataset(remembered):
            return get_source(remembered), "continuing with the dataset from this conversation"

    ranked = retriever.rank_datasets(question)
    for candidate_id, score in ranked:
        record = repository.get_dataset(candidate_id)
        if record:
            reason = f"matched on dataset metadata (relevance {score:.2f})"
            return from_dataset_record(record), reason

    datasets = repository.list_datasets()
    if datasets:
        return from_dataset_record(datasets[0]), "the most recently uploaded dataset"

    tables = list_tables()
    if tables:
        return from_sql_table(tables[0]), f"the '{tables[0]}' database table"

    raise DatasetNotFoundError("Upload a CSV or Excel file first, then ask a question about it.")


def starter_questions(dataset_id: str | None = None) -> list[str]:
    if dataset_id:
        try:
            profile = get_dataset(dataset_id).get("profile") or {}
            return profile.get("suggested_questions") or suggested_questions(profile)
        except DatasetNotFoundError:
            pass
    datasets = repository.list_datasets()
    if datasets:
        profile = datasets[0].get("profile") or {}
        return profile.get("suggested_questions") or suggested_questions(profile)
    return [
        "Upload a dataset to get started",
        "What can AInsight analyse?",
    ]
