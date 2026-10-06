"""Shared test fixtures.

``RAT_DATA_DIR`` is pointed at a throw-away sandbox *before* any ``app.*``
module is imported, so the test-suite can never touch real data.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

SANDBOX = Path(tempfile.mkdtemp(prefix="rat-tests-"))
os.environ["RAT_DATA_DIR"] = str(SANDBOX)

import pytest  # noqa: E402

from tests import fixtures  # noqa: E402


@pytest.fixture(scope="session")
def scratch_repo(tmp_path_factory) -> Path:
    repo = tmp_path_factory.mktemp("fixture-repos") / "scratch"
    fixtures.build_scratch_repo(repo)
    return repo


@pytest.fixture(scope="session")
def multi_repo(tmp_path_factory) -> Path:
    repo = tmp_path_factory.mktemp("fixture-repos") / "multi"
    fixtures.build_multi_author_repo(repo)
    return repo


@pytest.fixture(scope="session")
def app_client():
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    assert str(config.DATA_DIR) == str(SANDBOX), "sandbox data dir not honoured"
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def scratch_ingested(scratch_repo, app_client) -> int:
    """Scratch repo ingested directly via the analyzer (no worker); returns id."""
    from app import db
    from app.ingest import analyzer

    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO repos(name,kind,origin,dir,status,created_ts)"
        " VALUES (?,?,?,?,'ready',0)",
        ("scratch-metrics", "zip", str(scratch_repo), str(scratch_repo)),
    )
    conn.commit()
    repo_id = cur.lastrowid
    analyzer.analyze_repository(repo_id, scratch_repo)
    return repo_id
