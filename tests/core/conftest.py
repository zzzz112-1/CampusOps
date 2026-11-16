"""Offline Core fixtures; Main alone bootstraps isolated fictional test databases."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from campusops.bootstrap import create_application
from campusops.contracts.models import ExecutionMode
from .support import InjectedModel, MemoryReadonly
from tests.fixture_support import initialize_database


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def port():
    return MemoryReadonly()


@pytest.fixture
def model():
    return InjectedModel()


@pytest.fixture
def main_factory(tmp_path):
    """Main helper initializes a NEW offline fixture; no storage imports here."""
    path = tmp_path / "main-composition.db"
    initialize_database(path)

    def factory(core, client, mode=ExecutionMode.OFFLINE_MOCK_TEST):
        return create_application(path, orchestration=core, model_client=client, execution_mode=mode)

    return factory
