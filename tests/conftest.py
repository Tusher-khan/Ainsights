"""Shared pytest fixtures.

Every test runs against a temporary store and upload directory so the suite
never touches the developer's real datasets.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def isolated_environment(tmp_path_factory):
    base = tmp_path_factory.mktemp("ainsight")
    os.environ["DATABASE_URL"] = f"sqlite:///{base / 'test.db'}"
    os.environ["ANALYTICS_DATABASE_URL"] = f"sqlite:///{base / 'test.db'}"
    os.environ["CHROMA_PATH"] = str(base / "chroma")
    os.environ["UPLOAD_DIR"] = str(base / "uploads")
    os.environ["DEMO_MODE"] = "true"

    from config import Config

    Config.DATABASE_URL = os.environ["DATABASE_URL"]
    Config.ANALYTICS_DATABASE_URL = os.environ["ANALYTICS_DATABASE_URL"]
    Config.CHROMA_PATH = os.environ["CHROMA_PATH"]
    Config.UPLOAD_DIR = Path(os.environ["UPLOAD_DIR"])
    Config.DEMO_MODE = True
    Config.ensure_dirs()

    from app.database.connection import init_store

    init_store()
    yield base


@pytest.fixture(scope="session")
def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Order ID": [f"ORD-{i:03d}" for i in range(12)],
            "Order Date": pd.to_datetime(
                ["2024-01-05", "2024-01-18", "2024-02-02", "2024-02-14", "2024-03-01",
                 "2024-03-19", "2024-04-07", "2024-04-22", "2024-05-03", "2024-05-25",
                 "2024-06-08", "2024-06-30"]
            ),
            "Region": ["North", "South", "North", "East", "West", "South",
                       "North", "East", "West", "West", "South", "North"],
            "Category": ["Technology", "Furniture", "Technology", "Office", "Technology", "Furniture",
                         "Office", "Technology", "Furniture", "Office", "Technology", "Furniture"],
            "Sales": [100.0, 250.5, 75.25, 300.0, 60.0, 180.75,
                      220.0, 95.5, 410.25, 130.0, 275.0, 88.5],
            "Profit": [10.0, 25.0, 7.5, 30.0, 6.0, 18.0, 22.0, 9.5, 41.0, 13.0, 27.5, 8.8],
            "Quantity": [2, 5, 1, 7, 3, 4, 6, 2, 9, 3, 5, 2],
        }
    )


@pytest.fixture(scope="session")
def csv_path(tmp_path_factory, sample_frame) -> Path:
    path = tmp_path_factory.mktemp("files") / "orders.csv"
    sample_frame.to_csv(path, index=False)
    return path


@pytest.fixture(scope="session")
def excel_path(tmp_path_factory, sample_frame) -> Path:
    path = tmp_path_factory.mktemp("files") / "orders.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        sample_frame.to_excel(writer, index=False, sheet_name="Orders")
        sample_frame.head(4).to_excel(writer, index=False, sheet_name="Summary")
    return path


@pytest.fixture(scope="session")
def profile(sample_frame):
    from app.files.file_profiler import build_profile

    return build_profile(sample_frame.copy(), "Orders", "csv")


@pytest.fixture(scope="session")
def source(sample_frame, profile):
    from app.core.datasource import FrameDataSource

    class InMemorySource(FrameDataSource):
        def frame(self):
            return sample_frame.copy()

    return InMemorySource(id="ds_test", name="Orders", kind="dataframe", profile=profile)


@pytest.fixture(scope="session")
def registered_dataset(csv_path):
    """A dataset registered through the real service path."""
    from app.files.csv_loader import load_csv
    from app.files.file_profiler import build_profile
    from app.services.dataset_service import register_dataset
    from app.utils.helpers import slugify

    frame, meta = load_csv(csv_path)
    prof = build_profile(frame, "Orders", "csv", {"file_meta": meta})
    return register_dataset(
        {
            "name": "Orders",
            "slug": slugify("Orders"),
            "source_type": "csv",
            "file_path": str(csv_path),
            "size_bytes": csv_path.stat().st_size,
            "status": "ready",
        },
        prof,
    )


@pytest.fixture(scope="session")
def client(registered_dataset):
    """Flask test client.

    app.py is loaded by path because the project has both an `app.py` entry
    point and an `app/` package, and a plain `import app` would resolve to the
    package. This is also why the documented startup command is
    `python app.py` or `flask --app app.py run`.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("ainsight_entry", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    application = module.create_app()
    application.config["TESTING"] = True
    return application.test_client()
