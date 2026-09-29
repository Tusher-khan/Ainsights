"""Embeddings for metadata retrieval.

Three backends, chosen by what is installed:

1. `sentence-transformers` (all-MiniLM-L6-v2) — best semantic quality.
2. scikit-learn TF-IDF fitted on the metadata corpus — good lexical matching,
   no downloads required.
3. A deterministic hashing vectoriser with character n-grams — always works.

The active backend is reported on the System page so experiments can record it.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from typing import Sequence

import numpy as np

from app.utils.logger import get_logger

log = get_logger("rag.embeddings")

TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(str(text).lower())


class BaseEmbedder:
    name = "base"
    dimension = 256

    def fit(self, corpus: Sequence[str]) -> None:
        return None

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        raise NotImplementedError

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


class HashingEmbedder(BaseEmbedder):
    """Dependency-free hashing vectoriser over word and character n-grams."""

    name = "hashing-ngram"

    def __init__(self, dimension: int = 512) -> None:
        self.dimension = dimension

    def _features(self, text: str) -> list[str]:
        tokens = _tokenize(text)
        features = list(tokens)
        features += [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        compact = "".join(tokens)
        features += [compact[i : i + 4] for i in range(0, max(len(compact) - 3, 0))]
        return features

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature in self._features(text):
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dimension
                sign = 1.0 if digest[4] % 2 == 0 else -1.0
                matrix[row, index] += sign
            norm = np.linalg.norm(matrix[row])
            if norm:
                matrix[row] /= norm
        return matrix


class TfidfEmbedder(BaseEmbedder):
    """TF-IDF over the metadata corpus (scikit-learn)."""

    name = "tfidf-sklearn"

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(
            lowercase=True, ngram_range=(1, 2), sublinear_tf=True, min_df=1
        )
        self._fitted = False
        self._fallback = HashingEmbedder()

    def fit(self, corpus: Sequence[str]) -> None:
        corpus = [c for c in corpus if c and c.strip()]
        if not corpus:
            return
        self._vectorizer.fit(corpus)
        self._fitted = True
        self.dimension = len(self._vectorizer.vocabulary_)
        log.info("TF-IDF fitted on %s documents (%s features)", len(corpus), self.dimension)

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not self._fitted:
            return self._fallback.encode(texts)
        matrix = self._vectorizer.transform(list(texts)).toarray().astype(np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms


class SentenceTransformerEmbedder(BaseEmbedder):  # pragma: no cover - optional
    name = "sentence-transformers"

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dimension = self._model.get_sentence_embedding_dimension()

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(
            self._model.encode(list(texts), normalize_embeddings=True), dtype=np.float32
        )


_embedder: BaseEmbedder | None = None
_lock = threading.Lock()


def get_embedder() -> BaseEmbedder:
    global _embedder
    with _lock:
        if _embedder is not None:
            return _embedder
        try:
            _embedder = SentenceTransformerEmbedder()
        except Exception:
            try:
                _embedder = TfidfEmbedder()
            except Exception:
                _embedder = HashingEmbedder()
        log.info("Embedding backend: %s", _embedder.name)
        return _embedder


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0 or math.isnan(denominator):
        return 0.0
    return float(np.dot(a, b) / denominator)
