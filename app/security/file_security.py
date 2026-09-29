"""Upload security: extension allow-list, size limits, filename sanitisation,
path-traversal prevention and content sniffing.

Uploaded files are untrusted input. Nothing in a file is ever treated as an
instruction to the model (see app/security/prompt_security.py).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from config import Config
from app.utils.exceptions import FileValidationError
from app.utils.logger import get_logger

log = get_logger("security.file")

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_DOUBLE_EXT = re.compile(r"\.(exe|sh|bat|cmd|js|py|php|dll|jar)\.", re.I)


def sanitize_filename(filename: str) -> str:
    """Return a safe, flat filename. Never returns a path."""
    if not filename:
        raise FileValidationError("A file name is required.")
    name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    name = name.replace("\\", "/").split("/")[-1]          # strip any directory part
    name = name.replace("\x00", "")
    name = _SAFE_CHARS.sub("_", name).strip("._") or "upload"
    if _DOUBLE_EXT.search(name):
        raise FileValidationError("This file name looks unsafe. Rename it and try again.")
    return name[:120]


def validate_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in Config.ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(Config.ALLOWED_EXTENSIONS))
        raise FileValidationError(
            f"AInsight accepts {allowed} files. '{suffix or 'this file'}' is not supported."
        )
    return suffix


def validate_size(size_bytes: int) -> int:
    limit = Config.MAX_FILE_SIZE_MB * 1024 * 1024
    if size_bytes <= 0:
        raise FileValidationError("The uploaded file is empty.")
    if size_bytes > limit:
        raise FileValidationError(
            f"That file is {size_bytes / 1024 / 1024:.1f} MB. The limit is {Config.MAX_FILE_SIZE_MB} MB."
        )
    return size_bytes


def safe_upload_path(filename: str) -> Path:
    """Resolve a destination inside the upload directory, blocking traversal."""
    upload_dir = Path(Config.UPLOAD_DIR).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = (upload_dir / sanitize_filename(filename)).resolve()
    if upload_dir not in destination.parents and destination.parent != upload_dir:
        raise FileValidationError("Invalid upload destination.")
    return destination


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, counter = path.stem, path.suffix, 2
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def sniff_content(path: Path) -> str:
    """Reject binaries masquerading as CSV; identify XLSX by its zip header."""
    with open(path, "rb") as handle:
        head = handle.read(4096)
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        if head[:2] not in (b"PK", b"\xd0\xcf"):
            raise FileValidationError("That file is not a readable Excel workbook.")
        return "excel"
    if b"\x00" in head:
        raise FileValidationError("That file looks binary, not a text CSV.")
    return "text"


def validate_upload(filename: str, size_bytes: int) -> tuple[str, str]:
    safe = sanitize_filename(filename)
    suffix = validate_extension(safe)
    validate_size(size_bytes)
    log.info("Accepted upload candidate %s (%s bytes)", safe, size_bytes)
    return safe, suffix
