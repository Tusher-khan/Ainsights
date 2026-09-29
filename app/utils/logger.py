"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler

from config import Config

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(AIza[0-9A-Za-z_\-]{10,})"),
    re.compile(r"((?:api[_-]?key|token|password|secret)\s*[=:]\s*)([^\s,'\"]+)", re.I),
]

_CONFIGURED = False


class RedactingFilter(logging.Filter):
    """Prevents API keys, tokens and passwords from ever reaching the logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            if pattern.groups == 2:
                redacted = pattern.sub(r"\1***redacted***", redacted)
            else:
                redacted = pattern.sub("***redacted***", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    Config.ensure_dirs()
    root = logging.getLogger("ainsight")
    root.setLevel(getattr(logging, Config.LOG_LEVEL, logging.INFO))
    root.handlers.clear()
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.addFilter(RedactingFilter())
    root.addHandler(stream)

    try:
        file_handler = RotatingFileHandler(
            Config.LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.addFilter(RedactingFilter())
        root.addHandler(file_handler)
    except OSError:
        pass

    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"ainsight.{name}")
