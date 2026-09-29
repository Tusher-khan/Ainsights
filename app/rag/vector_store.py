"""Vector store for dataset metadata.

ChromaDB is used when installed. Otherwise a small persistent cosine-similarity
store (JSON on disk) provides the same `add` / `query` / `delete` interface, so
retrieval behaviour and evaluation remain reproducible offline.

Only *metadata* is ever embedded — never dataset rows.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np

from config import Config
from app.rag.embeddings import cosine_similarity, get_embedder
from app.utils.logger import get_logger

log = get_logger("rag.store")

try:  # pragma: no cover - optional
    import chromadb
    from chromadb.config import Settings

    CHROMA_AVAILABLE = True
except Exception:  # pragma: no cover
    chromadb = None
    CHROMA_AVAILABLE = False

COLLECTION = "ainsight_metadata"


class BaseVectorStore:
    backend = "base"

    def add(self, ids, documents, metadatas) -> None: ...
    def query(self, text: str, top_k: int = 5, where: dict | None = None) -> list[dict]: ...
    def delete_dataset(self, dataset_id: str) -> None: ...
    def count(self) -> int: ...
    def reset(self) -> None: ...


class LocalVectorStore(BaseVectorStore):
    """Cosine-similarity store persisted as JSON. No external service needed."""

    backend = "local-cosine"

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or Config.CHROMA_PATH) / "local_store.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._docs: dict[str, dict] = {}
        self._matrix: np.ndarray | None = None
        self._order: list[str] = []
        self._load()

    # --- persistence ----------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text("utf-8"))
            self._docs = payload.get("documents", {})
            log.info("Loaded %s metadata documents from disk", len(self._docs))
            self._reindex()
        except (ValueError, OSError) as exc:
            log.warning("Could not load vector store (%s); starting empty", exc)
            self._docs = {}

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps({"documents": self._docs}, ensure_ascii=False), "utf-8")
        except OSError as exc:  # pragma: no cover
            log.warning("Could not persist vector store: %s", exc)

    def _reindex(self) -> None:
        """Refit the embedder on the whole corpus, then re-embed everything."""
        embedder = get_embedder()
        self._order = list(self._docs.keys())
        if not self._order:
            self._matrix = None
            return
        corpus = [self._docs[i]["document"] for i in self._order]
        embedder.fit(corpus)
        self._matrix = embedder.encode(corpus)

    # --- interface ------------------------------------------------------
    def add(self, ids, documents, metadatas) -> None:
        with self._lock:
            for doc_id, document, metadata in zip(ids, documents, metadatas):
                self._docs[doc_id] = {"document": document, "metadata": metadata}
            self._reindex()
            self._save()

    def query(self, text: str, top_k: int = 5, where: dict | None = None) -> list[dict]:
        with self._lock:
            if self._matrix is None or not self._order:
                return []
            vector = get_embedder().encode_one(text)
            results = []
            for index, doc_id in enumerate(self._order):
                entry = self._docs[doc_id]
                if where and any(entry["metadata"].get(k) != v for k, v in where.items()):
                    continue
                results.append(
                    {
                        "id": doc_id,
                        "document": entry["document"],
                        "metadata": entry["metadata"],
                        "score": round(cosine_similarity(vector, self._matrix[index]), 4),
                    }
                )
            results.sort(key=lambda r: r["score"], reverse=True)
            return results[:top_k]

    def delete_dataset(self, dataset_id: str) -> None:
        with self._lock:
            removed = [k for k, v in self._docs.items() if v["metadata"].get("dataset_id") == dataset_id]
            for key in removed:
                self._docs.pop(key, None)
            if removed:
                self._reindex()
                self._save()

    def count(self) -> int:
        return len(self._docs)

    def reset(self) -> None:
        with self._lock:
            self._docs = {}
            self._matrix = None
            self._order = []
            self._save()


class ChromaVectorStore(BaseVectorStore):  # pragma: no cover - optional dependency
    backend = "chromadb"

    def __init__(self, path: str | None = None) -> None:
        self._client = chromadb.PersistentClient(
            path=str(path or Config.CHROMA_PATH), settings=Settings(anonymized_telemetry=False)
        )
        self._collection = self._client.get_or_create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine"}
        )

    def add(self, ids, documents, metadatas) -> None:
        self._collection.upsert(ids=list(ids), documents=list(documents), metadatas=list(metadatas))

    def query(self, text: str, top_k: int = 5, where: dict | None = None) -> list[dict]:
        response = self._collection.query(
            query_texts=[text], n_results=top_k, where=where or None
        )
        out = []
        for index, doc_id in enumerate(response.get("ids", [[]])[0]):
            distance = response.get("distances", [[0]])[0][index]
            out.append(
                {
                    "id": doc_id,
                    "document": response["documents"][0][index],
                    "metadata": response["metadatas"][0][index],
                    "score": round(1 - float(distance), 4),
                }
            )
        return out

    def delete_dataset(self, dataset_id: str) -> None:
        self._collection.delete(where={"dataset_id": dataset_id})

    def count(self) -> int:
        return self._collection.count()

    def reset(self) -> None:
        self._client.delete_collection(COLLECTION)
        self._collection = self._client.get_or_create_collection(COLLECTION)


_store: BaseVectorStore | None = None


def get_vector_store() -> BaseVectorStore:
    global _store
    if _store is not None:
        return _store
    if CHROMA_AVAILABLE:
        try:
            _store = ChromaVectorStore()
        except Exception as exc:  # pragma: no cover
            log.warning("ChromaDB unavailable (%s); using local vector store", exc)
            _store = LocalVectorStore()
    else:
        _store = LocalVectorStore()
    log.info("Vector store backend: %s", _store.backend)
    return _store
