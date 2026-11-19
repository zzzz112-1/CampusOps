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
        scratch = ROOT / ".tmp"
        scratch.mkdir(exist_ok=True)
        config.option.basetemp = str(scratch / ("pytest-" + uuid4().hex))


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
def never_open_the_real_owner_binding(monkeypatch):
    """The owner may bind while regression runs; tests only use isolated stores."""
    from campusops.security.state import SecurityStore, STATE_PATH
    original = SecurityStore._path_check
    def isolated_only(store):
        assert store.path != STATE_PATH, "Offline tests must not open the real owner security state"
        return original(store)
    monkeypatch.setattr(SecurityStore, "_path_check", isolated_only)
