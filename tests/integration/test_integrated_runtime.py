"""Main composition with real Core/UI code and explicit offline model injection."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier

import pytest

from campusops.agent_core import CoreSettings, GearShiftOrchestrator, LLMAdapter
from campusops.agent_core.errors import CoreFailure
from campusops.bootstrap import create_application, create_configured_application
from campusops.contracts.models import (
    AgentConfigPatch, ErrorCode, ExecutionMode, IncidentCreate, ModelResponse,
)
from campusops.ui.controller import UiController, can_decide
from campusops.storage.database import Database
from campusops.storage.seed import seed_demo

ROOT = Path(__file__).resolve().parents[2]


class OfflineModelClient:
    """INTEGRATION TEST ONLY: no production path imports this model double."""

    def __init__(self, *, revisions=0, fail=False):
        self.requests = []
        self.revisions = revisions
        self.verifications = 0
        self.fail = fail

    def complete(self, request):
        self.requests.append(request)
        if self.fail:
            raise CoreFailure(ErrorCode.PROVIDER_UNAVAILABLE, "MODEL_TIMEOUT", attempted=True)
        payload = json.loads(request.messages[1].content)
        role = json.loads(request.response_schema)["title"]
        if role == "PlanningDecision":
            result = dict(plan_id=payload["offered_plans"][0]["plan_id"], reason_codes=["stable_rank"],
                          summary="OFFLINE INTEGRATION TEST planning", tools=[])
        else:
            result = dict(reservation_ids=[row["id"] for row in payload["reservations"]],
                          evidence_ids=[payload["evidence_id"]], summary="OFFLINE INTEGRATION TEST evidence", tools=[])
            if role == "VerificationReport":
                revise = self.verifications < self.revisions
                self.verifications += 1
                result.update(verdict="revise" if revise else "pass",
                              reason_codes=["explanation_incomplete" if revise else "constraints_satisfied"])
        return ModelResponse(content=json.dumps(result), provider="INTEGRATION_TEST_INJECTED",
                             model_id="offline_mock_test", input_tokens=None, output_tokens=None)


def integrated(database, client, *, calls=6):
    return create_application(
        database.path, orchestration=GearShiftOrchestrator(CoreSettings(calls_per_run=calls)),
        model_client=client, execution_mode=ExecutionMode.OFFLINE_MOCK_TEST,
        provider="INTEGRATION_TEST_INJECTED", model_id="offline_mock_test",
    )


def register(controller):
    return controller.register_failure("cam-01", "2026-11-10T08:00:00+08:00",
                                       "Fictional integrated fault", confirmed=True)


def test_runtime_no_opt_in_never_reads_provider_or_changes_bookings(configured_context, monkeypatch):
    monkeypatch.setenv("CAMPUSOPS_REVIEW_LLM_ENABLED", "false")
    for name in ("CAMPUSOPS_REVIEW_MODEL_API_KEY", "CAMPUSOPS_REVIEW_MODEL_BASE_URL", "CAMPUSOPS_REVIEW_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)

    def denied(*args, **kwargs):
        raise AssertionError("Disabled runtime must not construct a model adapter")
    monkeypatch.setattr(LLMAdapter, "__init__", denied)
    service = create_configured_application(configured_context.database)
    before = service.list_reservations()
    incident = register(UiController(service, {}))
    result = service.start_workflow(incident.id)
    assert result.status == "FAILED" and result.error.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert service.get_run(result.run_id).model_calls == 0
    assert service.list_reservations() == before


def test_runtime_no_impact_succeeds_without_a_model(configured_context, monkeypatch):
    monkeypatch.setenv("CAMPUSOPS_REVIEW_LLM_ENABLED", "false")
    service = create_configured_application(configured_context.database)
    incident = service.register_equipment_failure(IncidentCreate(
        equipment_id="cam-01", effective_at="2026-11-12T00:00:00Z", reason="Fictional later fault"))
    result = service.start_workflow(incident.id)
    assert result.status == "NO_IMPACT" and service.get_run(result.run_id).model_calls == 0


def test_runtime_opt_in_without_configuration_fails_closed(configured_context, monkeypatch):
    monkeypatch.setenv("CAMPUSOPS_REVIEW_LLM_ENABLED", "true")
    for name in ("CAMPUSOPS_REVIEW_MODEL_API_KEY", "CAMPUSOPS_REVIEW_MODEL_BASE_URL", "CAMPUSOPS_REVIEW_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(CoreFailure) as caught:
        create_configured_application(configured_context.database)
    assert caught.value.error.code == ErrorCode.PROVIDER_UNAVAILABLE


def test_runtime_configured_binding_is_lazy_and_uses_single_adapter(configured_context, monkeypatch):
    from campusops import bootstrap

    for name, value in {
        "CAMPUSOPS_REVIEW_LLM_ENABLED": "true",
        "CAMPUSOPS_REVIEW_MODEL_API_KEY": "fictional-integration-test-key",
        "CAMPUSOPS_REVIEW_MODEL_BASE_URL": "https://fictional-provider.example/v1",
        "CAMPUSOPS_REVIEW_MODEL_ID": "fictional-model",
    }.items():
        monkeypatch.setenv(name, value)
    ledger = configured_context.budget / "usage.jsonl"
    captured = {}
    def record(*args, **kwargs):
        captured.update(kwargs)
        return "composition-only-test-sentinel"
    monkeypatch.setattr(bootstrap, "create_application", record)
    assert create_configured_application(configured_context.database) == "composition-only-test-sentinel"
    assert isinstance(captured["orchestration"], GearShiftOrchestrator)
    assert isinstance(captured["model_client"], LLMAdapter)
    assert captured["execution_mode"] == ExecutionMode.REAL_API
    assert captured["model_id"] == "fictional-model"
    # No complete() call, budget reservation or provider request happened.
    assert not ledger.exists()


def test_ui_core_main_approval_and_restart_readback(database):
    client = OfflineModelClient()
    service = integrated(database, client)
    controller = UiController(service, {})
    original = service.list_reservations()
    result = service.start_workflow(register(controller).id)
    current = controller.read_decision(result.run_id)
    assert can_decide(current.run, current.proposal)
    assert service.list_reservations() == original
    assert service.get_run(result.run_id).model_calls == len(client.requests) == 3
    assert {row.agent_id for row in service.list_run_steps(result.run_id) if row.model_calls} == {
        "impact-agent", "planner-agent", "verifier-agent",
    }
    assert all(row.execution_mode == ExecutionMode.OFFLINE_MOCK_TEST for row in service.list_run_steps(result.run_id))
    applied = controller.approve(result.run_id, "fictional-admin")
    assert applied.result.status == "APPLIED" and applied.current.run.status == "APPLIED"
    reloaded = create_application(database.path)
    key = controller.approval_key(result.proposal_id, "fictional-admin")
    assert reloaded.approve_proposal(result.proposal_id, "fictional-admin", key) == applied.result
    assert reloaded.list_reservations() == applied.current.reservations


def test_ui_core_rejection_is_persistent_and_bookings_unchanged(database):
    service = integrated(database, OfflineModelClient())
    controller = UiController(service, {})
    result = service.start_workflow(register(controller).id)
    before = service.list_reservations()
    outcome = controller.reject(result.run_id, "fictional-admin", "Fictional manual review")
    assert outcome.current.run.status == "REJECTED" and service.list_reservations() == before
    assert UiController(create_application(database.path), {}).read_decision(result.run_id).proposal.status == "REJECTED"


def test_ui_core_resource_change_before_approval_is_stale(database):
    service = integrated(database, OfflineModelClient())
    controller = UiController(service, {})
    result = service.start_workflow(register(controller).id)
    service.register_equipment_failure(IncidentCreate(
        equipment_id=result.changes[0].to_equipment_id, effective_at="2026-11-10T00:00:00Z",
        reason="Fictional replacement fault"))
    before = service.list_reservations()
    outcome = controller.approve(result.run_id, "fictional-admin")
    assert outcome.result.status == "STALE" and service.list_reservations() == before


def test_ui_core_two_competing_plans_cannot_both_apply(database):
    service = integrated(database, OfflineModelClient())
    incident = register(UiController(service, {}))
    plans = [service.start_workflow(incident.id) for _ in range(2)]
    barrier = Barrier(2)
    def approve(plan):
        controller = UiController(create_application(database.path), {})
        barrier.wait(timeout=5)
        return controller.approve(plan.run_id, "fictional-admin").result.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(approve, plans))
    assert sorted(outcomes) == ["APPLIED", "STALE"]


def test_ui_core_no_solution_and_disabled_agent_are_honest(database):
    client = OfflineModelClient()
    service = integrated(database, client)
    controller = UiController(service, {})
    incident = register(controller)
    service.update_agent_config("planner-agent", AgentConfigPatch(enabled=False))
    blocked = service.start_workflow(incident.id)
    assert blocked.status == "NEEDS_MANUAL" and not client.requests
    service.update_agent_config("planner-agent", AgentConfigPatch(enabled=True))
    for id_ in ("cam-02", "cam-03"):
        service.register_equipment_failure(IncidentCreate(
            equipment_id=id_, effective_at="2026-11-10T00:00:00Z", reason="Fictional all-candidates failure"))
    original = service.list_reservations()
    manual = service.start_workflow(incident.id)
    assert manual.status == "NEEDS_MANUAL" and "NO_FEASIBLE_PLAN" in manual.explanation
    assert service.list_reservations() == original
    assert not can_decide(controller.read_decision(manual.run_id).run, None)


@pytest.mark.parametrize("calls,expected,attempts", [(7, "WAIT_APPROVAL", 7), (6, "FAILED", 6)])
def test_real_core_feedback_loop_respects_replans_and_call_budget(database, calls, expected, attempts):
    client = OfflineModelClient(revisions=2)
    service = integrated(database, client, calls=calls)
    original = service.list_reservations()
    result = service.start_workflow(register(UiController(service, {})).id)
    assert result.status == expected
    run = service.get_run(result.run_id)
    assert run.retry_count == 2 and run.model_calls == len(client.requests) == attempts
    assert service.list_reservations() == original
    if expected == "FAILED":
        assert result.error.code == ErrorCode.BUDGET_EXCEEDED


def test_real_core_three_booking_dispatch_and_global_validation(tmp_path):
    payload = json.loads((ROOT / "assets/demo_seed.json").read_text(encoding="utf-8"))
    payload["reservations"][4]["equipment_id"] = "cam-01"
    asset = tmp_path / "fictional-three-booking-seed.json"
    asset.write_text(json.dumps(payload), encoding="utf-8")
    database = Database(tmp_path / "three-bookings.db")
    database.migrate()
    seed_demo(database, asset)
    service = integrated(database, OfflineModelClient())
    result = service.start_workflow(register(UiController(service, {})).id)
    assert result.status == "WAIT_APPROVAL" and len(result.changes) == 3
    assert result.validation.passed
    dispatch = next(row for row in service.list_run_steps(result.run_id) if row.node == "core.candidate_dispatch")
    assert "parallel_fan_out;tasks=3" in dispatch.decision_reason
    assert UiController(service, {}).approve(result.run_id, "fictional-admin").result.status == "APPLIED"


def test_provider_failure_keeps_bookings_and_logs_actual_attempt_count(database):
    service = integrated(database, OfflineModelClient(fail=True))
    original = service.list_reservations()
    result = service.start_workflow(register(UiController(service, {})).id)
    assert result.status == "FAILED" and result.error.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert service.get_run(result.run_id).model_calls == 1
    assert service.list_reservations() == original


def test_status_reports_seed_without_loading_model_config(review_context, monkeypatch, capsys):
    from campusops.cli import main

    monkeypatch.setattr("campusops.cli.get_context", lambda: review_context)
    monkeypatch.setenv("CAMPUSOPS_REVIEW_LLM_ENABLED", "true")
    monkeypatch.setattr("sys.argv", ["campusops", "status", "--db", str(review_context.database)])
    assert main() == 0
    output = capsys.readouterr().out
    assert "Equipment=10; Reservations=20" in output
    assert "Incidents=0; Runs=0" in output
    assert not (review_context.budget / "usage.jsonl").exists()
