"""Offline contract 1.1 quota reads; no provider, booking DB access or refunds."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import subprocess
import sys

import pytest

from campusops.agent_core import CoreSettings, LLMAdapter, ModelSettings
from campusops.agent_core import budget as budget_module, llm as llm_module
from campusops.agent_core.budget import BudgetLedger
from campusops.agent_core.errors import CoreFailure
from campusops.contracts.models import ErrorCode, IncidentCreate
from campusops.contracts.ports import ModelBudgetPort


def adapter(ledger, limits=None):
    return LLMAdapter(ModelSettings(api_key="TEST_INJECTED", base_url="https://provider.example.invalid/v1",
                                   model_id="fictional-model", enabled=False,
                                   limits=limits or CoreSettings()), budget=ledger)


def assert_unknown(snapshot):
    assert snapshot.status == "UNAVAILABLE"
    assert all(getattr(snapshot, name) is None for name in (
        "per_run_limit", "daily_limit", "run_reserved", "daily_reserved", "run_remaining", "daily_remaining",
    ))
    assert snapshot.error.code == ErrorCode.BUDGET_EXCEEDED


def test_missing_ledger_is_zero_reservations_without_reserve_or_network(tmp_path, monkeypatch):
    ledger = BudgetLedger(tmp_path)
    def denied(*args, **kwargs):
        pytest.fail("A readonly budget query must never reserve or contact a provider")
    monkeypatch.setattr(ledger, "reserve", denied)
    monkeypatch.setattr(llm_module, "OpenAI", denied)
    client = adapter(ledger)
    assert isinstance(client, ModelBudgetPort)
    for _ in range(2):
        snapshot = client.get_model_budget("run_one")
        assert snapshot.status == "AVAILABLE" and snapshot.scope == "current_worktree"
        assert (snapshot.run_reserved, snapshot.daily_reserved) == (0, 0)
        assert (snapshot.run_remaining, snapshot.daily_remaining) == (6, 30)
        assert snapshot.observed_at.date() == snapshot.utc_date
    # Only lock metadata may be created; queries never create/rebuild the journal.
    assert not (tmp_path / "usage.jsonl").exists()


def test_queries_return_persistent_counts_and_do_not_change_journal(tmp_path):
    limits = CoreSettings()
    ledger = BudgetLedger(tmp_path)
    for run_id in ("run_one", "run_one", "run_two"):
        ledger.reserve(run_id, limits)
    path = tmp_path / "usage.jsonl"
    before = path.read_bytes()
    for _ in range(3):
        snapshot = adapter(BudgetLedger(tmp_path)).get_model_budget("run_one")
        assert (snapshot.run_reserved, snapshot.daily_reserved) == (2, 3)
        assert (snapshot.run_remaining, snapshot.daily_remaining) == (4, 27)
    assert path.read_bytes() == before


def test_observation_is_captured_inside_the_reservation_lock(tmp_path, monkeypatch):
    ledger = BudgetLedger(tmp_path)
    class LockedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert ledger._lock.locked(), "Observation must be sampled with counters under the same lock"
            return datetime.now(timezone.utc)
    monkeypatch.setattr(budget_module, "datetime", LockedClock)
    assert ledger.get_model_budget("run_one", CoreSettings()).status == "AVAILABLE"


def test_utc_rollover_keeps_run_total_and_restarts_daily_total(tmp_path, monkeypatch):
    limits = CoreSettings()
    ledger = BudgetLedger(tmp_path)
    ledger.reserve("run_one", limits)
    class NextDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=1)
    monkeypatch.setattr(budget_module, "datetime", NextDay)
    before = ledger.get_model_budget("run_one", limits)
    assert (before.run_reserved, before.daily_reserved) == (1, 0)
    ledger.reserve("run_one", limits)
    after = ledger.get_model_budget("run_one", limits)
    assert (after.run_reserved, after.daily_reserved) == (2, 1)
    assert (after.run_remaining, after.daily_remaining) == (4, 29)


def test_lowered_limits_clamp_remaining_without_rewriting_reservations(tmp_path):
    ledger = BudgetLedger(tmp_path)
    for _ in range(2):
        ledger.reserve("run_one", CoreSettings())
    before = (tmp_path / "usage.jsonl").read_bytes()
    snapshot = ledger.get_model_budget("run_one", CoreSettings(calls_per_run=1, daily_calls=1))
    assert snapshot.status == "AVAILABLE" and (snapshot.run_reserved, snapshot.daily_reserved) == (2, 2)
    assert (snapshot.run_remaining, snapshot.daily_remaining) == (0, 0)
    assert (tmp_path / "usage.jsonl").read_bytes() == before


@pytest.mark.parametrize("data", [
    b"TEST_PRIVATE_INCOMPLETE", b'{"version":1}\n', b"\n",
    b'{"version":1,"utc_date":"2026-1-1","run_id":"run_one","daily_count":1,"run_count":1}\n',
    b'{"version":1,"utc_date":"2026-11-10","run_id":"run_one","daily_count":2,"run_count":1}\n',
])
def test_corruption_remains_unknown_and_preserves_original_bytes(tmp_path, data):
    path = tmp_path / "usage.jsonl"
    path.write_bytes(data)
    snapshot = adapter(BudgetLedger(tmp_path)).get_model_budget("run_one")
    assert_unknown(snapshot)
    assert "TEST_PRIVATE" not in snapshot.model_dump_json()
    assert path.read_bytes() == data
    with pytest.raises(CoreFailure, match="BUDGET_LEDGER_UNAVAILABLE_OR_INVALID"):
        BudgetLedger(tmp_path).reserve("run_one", CoreSettings())
    assert path.read_bytes() == data


def test_read_failures_do_not_expose_paths_or_silently_return_zero(tmp_path, monkeypatch):
    ledger = BudgetLedger(tmp_path)
    @contextmanager
    def denied():
        raise OSError("TEST_PRIVATE_PATH_AND_PROVIDER_ERROR")
        yield
    monkeypatch.setattr(ledger, "_exclusive", denied)
    snapshot = ledger.get_model_budget("run_one", CoreSettings())
    assert_unknown(snapshot)
    assert "TEST_PRIVATE" not in snapshot.model_dump_json()


def test_other_process_cannot_read_unlocked_during_a_reservation(tmp_path):
    code = "\n".join([
        "from pathlib import Path", "import sys",
        "from campusops.agent_core.budget import BudgetLedger",
        "from campusops.agent_core.config import CoreSettings",
        "snapshot=BudgetLedger(Path(sys.argv[1])).get_model_budget('run_one', CoreSettings())",
        "assert snapshot.status == 'UNAVAILABLE' and snapshot.daily_reserved is None",
        "print('UNAVAILABLE: counters remain unknown while the OS lock is held')",
    ])
    with BudgetLedger(tmp_path)._exclusive():
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "UNAVAILABLE" in result.stdout


def test_concurrent_readers_see_complete_monotonic_reservations(tmp_path):
    limits = CoreSettings(calls_per_run=50, daily_calls=50)
    def writer():
        for _ in range(20):
            BudgetLedger(tmp_path).reserve("run_one", limits)
    def reader():
        counts = []
        for _ in range(20):
            snapshot = BudgetLedger(tmp_path).get_model_budget("run_one", limits)
            assert snapshot.status == "AVAILABLE" and snapshot.run_reserved == snapshot.daily_reserved
            assert snapshot.observed_at.date() == snapshot.utc_date
            counts.append(snapshot.run_reserved)
        assert counts == sorted(counts)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(writer), pool.submit(reader), pool.submit(reader)]
        for future in futures:
            future.result(timeout=15)
    snapshot = BudgetLedger(tmp_path).get_model_budget("run_one", limits)
    assert (snapshot.run_reserved, snapshot.daily_reserved) == (20, 20)
    assert len((tmp_path / "usage.jsonl").read_text().splitlines()) == 20


def test_invalid_run_id_never_creates_ledger(tmp_path):
    directory = tmp_path / "quota"
    with pytest.raises(CoreFailure, match="INVALID_BUDGET_RUN_ID"):
        BudgetLedger(directory).get_model_budget("../TEST_PRIVATE", CoreSettings())
    assert not directory.exists()


def test_main_public_port_delegates_without_model_calls_or_audit_writes(main_factory, tmp_path, monkeypatch):
    ledger = BudgetLedger(tmp_path / "quota")
    client = adapter(ledger)
    def denied(*args, **kwargs):
        pytest.fail("Readonly Main budget lookup must not invoke complete or reserve")
    monkeypatch.setattr(client, "complete", denied)
    service = main_factory(None, client)
    incident = service.register_equipment_failure(IncidentCreate(
        equipment_id="cam-01", effective_at="2099-11-10T00:00:00Z", reason="Fictional no-impact budget lookup",
    ))
    plan = service.start_workflow(incident.id)
    assert plan.status == "NO_IMPACT"
    ledger.reserve(plan.run_id, client.settings.limits)
    monkeypatch.setattr(ledger, "reserve", denied)
    bookings, steps = service.list_reservations(), service.list_run_steps(plan.run_id)
    snapshot = service.get_model_budget(plan.run_id)
    assert snapshot.status == "AVAILABLE" and snapshot.run_reserved == 1
    assert service.get_run(plan.run_id).model_calls == 0
    assert service.list_reservations() == bookings and service.list_run_steps(plan.run_id) == steps
