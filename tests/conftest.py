"""Project-wide test safety: D-drive scratch, network disabled by default."""

from pathlib import Path
import socket
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


def pytest_addoption(parser):
    parser.addoption("--run-real-api", action="store_true", default=False,
                     help="Explicitly opt into separately marked real_api tests; never enabled by default")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_api: separately authorized real network/model acceptance")
    if config.option.basetemp is None:
        config.option.basetemp = str(ROOT / ".tmp" / ("pytest-" + uuid4().hex))


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-real-api"):
        for item in items:
            if item.get_closest_marker("real_api"):
                item.add_marker(pytest.mark.skip(reason="Real API tests require explicit opt-in"))


@pytest.fixture(autouse=True)
def offline_by_default(request, monkeypatch):
    if not (request.node.get_closest_marker("real_api") and request.config.getoption("--run-real-api")):
        def denied(*args, **kwargs):
            raise AssertionError("Network/model calls are forbidden in offline tests")
        monkeypatch.setattr(socket.socket, "connect", denied)
        monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture(autouse=True)
def explicit_authorized_context_for_existing_business_ui_tests(request, monkeypatch):
    """Preserve all business assertions without adding a production bypass.

    New test_totp_* tests do NOT use this fixture's authorization replacement.
    No real owner binding is read; this injection exists only in test code.
    """
    if request.node.path.parent.name == "ui" or request.node.path.name == "test_streamlit_entry.py":
        from campusops.security import streamlit_gate
        from tests.fixture_support import PreauthorizedBusinessTestContext
        monkeypatch.setattr(streamlit_gate, "require_ui_access", lambda: PreauthorizedBusinessTestContext())
        monkeypatch.setattr(streamlit_gate, "touch_user_activity", lambda: None)
        monkeypatch.setattr(streamlit_gate, "render_lock_button", lambda: None)


@pytest.fixture(autouse=True)
def never_open_the_real_owner_binding(monkeypatch):
    """The owner may bind while regression runs; tests only use isolated stores."""
    from campusops.security.state import SecurityStore, STATE_PATH
    original = SecurityStore._path_check
    def isolated_only(store):
        assert store.path != STATE_PATH, "Offline tests must not open the real owner security state"
        return original(store)
    monkeypatch.setattr(SecurityStore, "_path_check", isolated_only)
