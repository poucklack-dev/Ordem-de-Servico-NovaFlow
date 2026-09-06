"""Run every test against a fresh database, never the running application's DB."""

import os
import sqlite3
import gc
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


_database_directory = TemporaryDirectory(prefix="novaflow-tests-", ignore_cleanup_errors=True)
_database_path = Path(_database_directory.name) / "acceptance.db"
_template_path = Path(_database_directory.name) / "seed-template.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_database_path.as_posix()}"
os.environ["DEMO_SEED"] = "true"

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def seeded_template():
    assert Path(engine.url.database).resolve() == _database_path.resolve()
    Base.metadata.drop_all(engine)
    with TestClient(app):
        pass
    engine.dispose()
    with sqlite3.connect(_database_path) as source, sqlite3.connect(_template_path) as target:
        source.backup(target)


@pytest.fixture(autouse=True)
def fresh_database(seeded_template):
    assert Path(engine.url.database).resolve() == _database_path.resolve()
    engine.dispose()
    with sqlite3.connect(_template_path) as source, sqlite3.connect(_database_path) as target:
        source.backup(target)
    yield
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    gc.collect()
    try:
        _database_directory.cleanup()
    except PermissionError:
        # Windows can keep a SQLite handle alive briefly after TestClient closes.
        # The OS will remove this temporary directory after the process exits.
        pass
