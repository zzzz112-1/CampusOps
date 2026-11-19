"""Fresh fictional databases shared by the integration suites."""

import os
from pathlib import Path
import shutil

import pytest

from campusops.bootstrap import create_application
from campusops.contracts.models import IncidentCreate
from campusops.reviewer import get_context
from campusops.storage.database import Database
from tests.fixture_support import initialize_database

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def database(tmp_path):
    """Each test gets an independent copy of the fictional seed."""
    path = tmp_path / "integration.db"
    initialize_database(path)
    return Database(path)


@pytest.fixture
def service(database):
    return create_application(database.path, seed_asset=ROOT / "assets/demo_seed.json")


@pytest.fixture
def fault(service):
    return service.register_equipment_failure(IncidentCreate(
        equipment_id="cam-01",
        effective_at="2026-11-10T00:00:00Z",
        reason="Fictional integration test fault",
    ))


@pytest.fixture
def offline_service(database):
    from .test_integrated_runtime import OfflineModelClient, integrated

    return integrated(database, OfflineModelClient())


@pytest.fixture
def review_context(tmp_path):
    root = tmp_path / "review-copy"
    root.mkdir()
    for name in ("reviewer-build.json", "pyproject.toml", "AGENTS.md", "assets/demo_seed.json", "docs/project-spec-v2.md"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return get_context(root)


@pytest.fixture(autouse=True)
def preserve_process_environment():
    previous = dict(os.environ)
    try:
        yield
    finally:
        for name in os.environ.keys() - previous.keys():
            del os.environ[name]
        for name, value in previous.items():
            if os.environ.get(name) != value:
                os.environ[name] = value


@pytest.fixture
def configured_context(review_context, monkeypatch):
    from campusops.agent_core import budget

    monkeypatch.setattr("campusops.reviewer.get_context", lambda: review_context)
    monkeypatch.setattr("campusops.security.streamlit_gate.get_context", lambda: review_context)
    # Resolve the budget location as an editable install in this fresh fixture.
    monkeypatch.setattr(budget, "__file__", str(review_context.root / "src/campusops/agent_core/budget.py"))
    return review_context
