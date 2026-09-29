"""Upload handling: validate → store → load → profile → index → register."""

from __future__ import annotations

from pathlib import Path

from config import Config
from app.database import repository
from app.files.csv_loader import load_csv
from app.files.excel_loader import list_sheets, load_excel
from app.files.file_profiler import build_profile
from app.security.file_security import (
    safe_upload_path,
    sniff_content,
    unique_path,
    validate_upload,
)
from app.services.dataset_service import register_dataset
from app.utils.exceptions import FileProcessingError
from app.utils.helpers import slugify
from app.utils.logger import get_logger

log = get_logger("services.file")


def save_upload(file_storage) -> tuple[Path, str]:
    """Persist an uploaded file securely and return (path, safe_name)."""
    filename = file_storage.filename or ""
    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)

    safe_name, _suffix = validate_upload(filename, size)
    destination = unique_path(safe_upload_path(safe_name))
    file_storage.save(destination)
    sniff_content(destination)
    log.info("Stored upload at %s (%s bytes)", destination.name, size)
    return destination, safe_name


def load_any(path: Path, sheet_name: str | None = None):
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        return load_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return load_excel(path, sheet_name)
    raise FileProcessingError(f"Unsupported file type '{suffix}'.")


def process_upload(file_storage, display_name: str | None = None, sheet_name: str | None = None) -> dict:
    """Full pipeline for one uploaded file. Returns the registered dataset."""
    path, safe_name = save_upload(file_storage)
    frame, meta = load_any(path, sheet_name)
    name = display_name or Path(safe_name).stem.replace("_", " ").strip() or safe_name
    source_type = "excel" if meta.get("file_type") == "excel" else "csv"

    profile = build_profile(frame, name, source_type, extra={"file_meta": meta})
    dataset = register_dataset(
        {
            "name": name,
            "slug": slugify(name),
            "source_type": source_type,
            "file_path": str(path),
            "sheet_name": meta.get("sheet_name"),
            "size_bytes": path.stat().st_size,
            "status": "ready",
        },
        profile,
    )
    dataset["file_meta"] = meta
    return dataset


def profile_existing_file(dataset: dict) -> dict:
    path = Path(dataset["file_path"])
    frame, meta = load_any(path, dataset.get("sheet_name"))
    return build_profile(frame, dataset["name"], dataset["source_type"], extra={"file_meta": meta})


def inspect_workbook(path: Path) -> list[str]:
    return list_sheets(path)


def autoload_examples() -> list[dict]:
    """Register any file dropped into data/examples or data/ that is not yet known."""
    registered = []
    known = {Path(d["file_path"]).resolve() for d in repository.list_datasets() if d.get("file_path")}
    candidates = []
    for directory in (Config.EXAMPLES_DIR, Config.DATA_DIR):
        if Path(directory).exists():
            candidates += [p for p in Path(directory).glob("*") if p.suffix.lower() in Config.ALLOWED_EXTENSIONS]

    for path in sorted(set(candidates)):
        if path.resolve() in known:
            continue
        try:
            frame, meta = load_any(path)
            name = path.stem.replace("_", " ").title()
            profile = build_profile(frame, name, "excel" if meta.get("file_type") == "excel" else "csv", {"file_meta": meta})
            registered.append(
                register_dataset(
                    {
                        "name": name,
                        "slug": slugify(name),
                        "source_type": "excel" if meta.get("file_type") == "excel" else "csv",
                        "file_path": str(path),
                        "sheet_name": meta.get("sheet_name"),
                        "size_bytes": path.stat().st_size,
                        "status": "ready",
                    },
                    profile,
                )
            )
            log.info("Auto-registered dataset from %s", path.name)
        except Exception as exc:  # a bad example file must not stop startup
            log.warning("Could not auto-load %s: %s", path.name, exc)
    return registered
