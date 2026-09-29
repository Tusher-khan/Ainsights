"""AInsight configuration.

All settings come from environment variables (loaded from .env when present).
Nothing here is hard-coded to a single machine, provider or dataset.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is optional at runtime
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv always present in requirements
    pass


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


BASE_DIR = Path(__file__).resolve().parent


class Config:
    """Runtime configuration for the AInsight platform."""

    # --- paths -----------------------------------------------------------
    BASE_DIR = BASE_DIR
    DATA_DIR = BASE_DIR / "data"
    RAW_DIR = DATA_DIR / "raw"
    PROCESSED_DIR = DATA_DIR / "processed"
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
    EXAMPLES_DIR = DATA_DIR / "examples"
    DATABASE_DIR = BASE_DIR / "database"
    LOG_DIR = BASE_DIR / "logs"
    LOG_FILE = LOG_DIR / "application.log"

    # --- flask -----------------------------------------------------------
    SECRET_KEY = os.getenv("SECRET_KEY", "ainsight-dev-secret-change-me")
    DEBUG = _bool("DEBUG", False)
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = _int("PORT", 5000)

    # --- storage ---------------------------------------------------------
    # Application store (datasets, history, feedback, conversations).
    DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATABASE_DIR / 'analytics.db'}")
    # Optional analytical SQL database the SQL agent may query (read-only).
    ANALYTICS_DATABASE_URL = os.getenv("ANALYTICS_DATABASE_URL", DATABASE_URL)
    CHROMA_PATH = os.getenv("CHROMA_PATH", str(BASE_DIR / "data" / "processed" / "chroma"))

    # --- uploads ---------------------------------------------------------
    MAX_FILE_SIZE_MB = _int("MAX_FILE_SIZE_MB", 50)
    MAX_CONTENT_LENGTH = MAX_FILE_SIZE_MB * 1024 * 1024
    ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".tsv"}

    # --- llm -------------------------------------------------------------
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "demo").strip().lower()
    MODEL_NAME = os.getenv("MODEL_NAME", "")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
    LLM_TIMEOUT_SECONDS = _int("LLM_TIMEOUT_SECONDS", 60)
    LLM_MAX_TOKENS = _int("LLM_MAX_TOKENS", 1200)
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.0"))

    # DEMO_MODE runs the whole pipeline with a deterministic local planner,
    # so the platform is fully usable without any external API.
    DEMO_MODE = _bool("DEMO_MODE", True)

    # --- pipeline --------------------------------------------------------
    MAX_QUERY_RETRIES = _int("MAX_QUERY_RETRIES", 2)
    MAX_RESULT_ROWS = _int("MAX_RESULT_ROWS", 5000)
    MAX_CHART_POINTS = _int("MAX_CHART_POINTS", 300)
    SAMPLE_VALUES_PER_COLUMN = _int("SAMPLE_VALUES_PER_COLUMN", 5)
    RAG_TOP_K = _int("RAG_TOP_K", 6)
    MEMORY_TURNS = _int("MEMORY_TURNS", 8)
    QUERY_TIMEOUT_SECONDS = _int("QUERY_TIMEOUT_SECONDS", 30)

    # --- logging ---------------------------------------------------------
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    @classmethod
    def sqlite_path(cls, url: str | None = None) -> Path:
        """Return the filesystem path for a sqlite:/// URL."""
        url = url or cls.DATABASE_URL
        if url.startswith("sqlite:///"):
            return Path(url[len("sqlite:///") :])
        raise ValueError(f"Not a SQLite URL: {url}")

    @classmethod
    def is_sqlite(cls, url: str | None = None) -> bool:
        return (url or cls.DATABASE_URL).startswith("sqlite:///")

    @classmethod
    def ensure_dirs(cls) -> None:
        for path in (
            cls.DATA_DIR,
            cls.RAW_DIR,
            cls.PROCESSED_DIR,
            cls.UPLOAD_DIR,
            cls.EXAMPLES_DIR,
            cls.DATABASE_DIR,
            cls.LOG_DIR,
            Path(cls.CHROMA_PATH),
        ):
            Path(path).mkdir(parents=True, exist_ok=True)

    @classmethod
    def public_summary(cls) -> dict:
        """Configuration safe to expose through the API (never secrets)."""
        return {
            "llm_provider": cls.LLM_PROVIDER,
            "model_name": cls.MODEL_NAME or "(provider default)",
            "demo_mode": cls.DEMO_MODE,
            "debug": cls.DEBUG,
            "max_file_size_mb": cls.MAX_FILE_SIZE_MB,
            "max_query_retries": cls.MAX_QUERY_RETRIES,
            "rag_top_k": cls.RAG_TOP_K,
            "database_backend": "sqlite" if cls.is_sqlite() else "postgresql",
            "api_key_configured": bool(
                cls.OPENAI_API_KEY or cls.GEMINI_API_KEY or cls.ANTHROPIC_API_KEY
            ),
        }


Config.ensure_dirs()
