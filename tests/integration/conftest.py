"""Fixtures for integration tests that use fictional CampusOps data."""

from pathlib import Path

import pytest

from campusops.bootstrap import create_application
from campusops.contracts.models import Incident, IncidentCreate
from campusops.contracts.ports import ApplicationPort
from campusops.storage.database import Database
from tests.fixture_support import initialize_database


@pytest.fixture
def database(tmp_path: Path) -> Database:
    path = tmp_path / "campusops.db"
    initialize_database(path)
    return Database(path)


@pytest.fixture
def service(database: Database) -> ApplicationPort:
    return create_application(database.path)


@pytest.fixture
def offline_service(database: Database) -> ApplicationPort:
    from .test_integrated_runtime import OfflineModelClient, integrated

    return integrated(database, OfflineModelClient())


@pytest.fixture
def fault(service: ApplicationPort) -> Incident:
    return service.register_equipment_failure(IncidentCreate(
        equipment_id="cam-01",
        effective_at="2026-11-10T08:00:00+08:00",
        reason="Fictional integration test fault",
    ))
