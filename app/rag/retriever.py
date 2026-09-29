"""Retrieval layer.

RAG supplies CONTEXT for planning — which dataset and which columns are
relevant. It is never the source of numerical truth; every number comes from
SQL or pandas execution.
"""

from __future__ import annotations

from config import Config
from app.rag.vector_store import get_vector_store
from app.utils.logger import get_logger

log = get_logger("rag.retriever")


def retrieve(question: str, top_k: int | None = None, dataset_id: str | None = None) -> list[dict]:
    top_k = top_k or Config.RAG_TOP_K
    where = {"dataset_id": dataset_id} if dataset_id else None
    try:
        hits = get_vector_store().query(question, top_k=top_k, where=where)
    except Exception as exc:  # retrieval must never break the pipeline
        log.warning("Retrieval failed (%s); continuing without RAG context", exc)
        return []
    log.info("Retrieved %s metadata documents (dataset filter=%s)", len(hits), dataset_id or "none")
    return hits


def rank_datasets(question: str, top_k: int = 12) -> list[tuple[str, float]]:
    """Score datasets by aggregated metadata similarity (dataset routing)."""
    scores: dict[str, float] = {}
    for hit in retrieve(question, top_k=top_k):
        dataset_id = hit["metadata"].get("dataset_id")
        if not dataset_id:
            continue
        weight = 1.25 if hit["metadata"].get("doc_type") == "dataset" else 1.0
        scores[dataset_id] = scores.get(dataset_id, 0.0) + max(hit["score"], 0.0) * weight
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def context_block(hits: list[dict], limit: int = 8) -> str:
    """Render retrieved metadata for a prompt."""
    lines = []
    for hit in hits[:limit]:
        meta = hit["metadata"]
        prefix = f"[{meta.get('dataset_name', '?')}"
        if meta.get("column"):
            prefix += f".{meta['column']}"
        prefix += f" | score {hit['score']:.2f}]"
        lines.append(f"{prefix} {hit['document']}")
    return "\n".join(lines)


def relevant_columns(hits: list[dict]) -> list[str]:
    return [h["metadata"]["column"] for h in hits if h["metadata"].get("column")]
