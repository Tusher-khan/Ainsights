"""In-process DataFrame cache.

Loading a large CSV on every question would be wasteful, so frames are cached
by dataset id with an LRU bound. The cache is the only place raw data lives in
memory; the LLM never sees it.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import pandas as pd

from app.files.csv_loader import load_csv
from app.files.excel_loader import load_excel
from app.files.file_profiler import coerce_datetimes
from app.utils.exceptions import DatasetNotFoundError, FileProcessingError
from app.utils.logger import get_logger

log = get_logger("files.frames")

MAX_CACHED_FRAMES = 6


class DataFrameManager:
    def __init__(self) -> None:
        self._frames: OrderedDict[str, pd.DataFrame] = OrderedDict()
        self._lock = threading.Lock()

    def load(self, dataset: dict) -> pd.DataFrame:
        dataset_id = dataset["id"]
        with self._lock:
            if dataset_id in self._frames:
                self._frames.move_to_end(dataset_id)
                return self._frames[dataset_id]

        path = Path(dataset.get("file_path") or "")
        if not path.exists():
            raise DatasetNotFoundError(
                f"The file for '{dataset.get('name')}' is missing. Upload it again."
            )
        suffix = path.suffix.lower()
        if suffix in {".csv", ".tsv"}:
            frame, _ = load_csv(path)
        elif suffix in {".xlsx", ".xls"}:
            frame, _ = load_excel(path, dataset.get("sheet_name"))
        else:
            raise FileProcessingError(f"Unsupported file type '{suffix}'.")
        frame = coerce_datetimes(frame)

        with self._lock:
            self._frames[dataset_id] = frame
            self._frames.move_to_end(dataset_id)
            while len(self._frames) > MAX_CACHED_FRAMES:
                evicted, _ = self._frames.popitem(last=False)
                log.info("Evicted cached frame %s", evicted)
        return frame

    def invalidate(self, dataset_id: str) -> None:
        with self._lock:
            self._frames.pop(dataset_id, None)

    def clear(self) -> None:
        with self._lock:
            self._frames.clear()

    @property
    def cached_ids(self) -> list[str]:
        return list(self._frames.keys())


frame_manager = DataFrameManager()
