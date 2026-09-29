"""Builds the metadata documents that RAG retrieves.

One document per dataset (an overview) plus one per column. Documents contain
semantic descriptions, sample values and statistics — never bulk data.
"""

from __future__ import annotations

from app.rag.vector_store import get_vector_store
from app.security.prompt_security import neutralize_data
from app.utils.logger import get_logger

log = get_logger("rag.metadata")


def dataset_document(dataset_id: str, profile: dict) -> tuple[str, str, dict]:
    name = profile.get("name", "dataset")
    columns = ", ".join(profile.get("column_names", [])[:40])
    text = (
        f"Dataset '{name}' is a {profile.get('source_type', 'file')} source with "
        f"{profile.get('row_count', 0)} rows and {profile.get('column_count', 0)} columns. "
        f"Columns: {columns}. "
        f"Numeric measures: {', '.join(profile.get('measures', [])) or 'none'}. "
        f"Categorical dimensions: {', '.join(profile.get('dimensions', [])) or 'none'}. "
        f"Date columns: {', '.join(profile.get('date_columns', [])) or 'none'}."
    )
    metadata = {
        "dataset_id": dataset_id,
        "dataset_name": name,
        "doc_type": "dataset",
        "column": "",
    }
    return f"{dataset_id}::__dataset__", neutralize_data(text, 2000), metadata


def column_documents(dataset_id: str, profile: dict) -> list[tuple[str, str, dict]]:
    name = profile.get("name", "dataset")
    documents = []
    for column in profile.get("columns", []):
        text = (
            f"In dataset '{name}', {column['description']} "
            f"Semantic role: {column['semantic_type']}. "
            f"Distinct values: {column['unique_count']}. Missing: {column['null_percentage']}%."
        )
        documents.append(
            (
                f"{dataset_id}::{column['name']}",
                neutralize_data(text, 1500),
                {
                    "dataset_id": dataset_id,
                    "dataset_name": name,
                    "doc_type": "column",
                    "column": column["name"],
                    "semantic_type": column["semantic_type"],
                },
            )
        )
    return documents


def index_dataset(dataset_id: str, profile: dict) -> int:
    """(Re)index one dataset's metadata into the vector store."""
    store = get_vector_store()
    store.delete_dataset(dataset_id)
    docs = [dataset_document(dataset_id, profile)] + column_documents(dataset_id, profile)
    store.add([d[0] for d in docs], [d[1] for d in docs], [d[2] for d in docs])
    log.info("Indexed %s metadata documents for dataset %s", len(docs), dataset_id)
    return len(docs)


def remove_dataset(dataset_id: str) -> None:
    get_vector_store().delete_dataset(dataset_id)
    log.info("Removed metadata for dataset %s", dataset_id)
