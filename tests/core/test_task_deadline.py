"""TEST_INJECTED monotonic time; no sleeping, network or real model calls."""

import pytest

from campusops.agent_core import CoreSettings, GearShiftOrchestrator
from campusops.agent_core import runtime
from campusops.agent_core.errors import CoreFailure
from campusops.agent_core.registry import select_agents
from campusops.agent_core.roles import ImpactReport
from campusops.contracts.models import IncidentCreate, RunStatus
from .support import InjectedModel, MemoryReadonly


class InjectedClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    value = InjectedClock()
    monkeypatch.setattr(runtime, "monotonic", value, raising=False)
    return value


def execute(port, model):
    return GearShiftOrchestrator().run_incident(port.incident.id, port.run_id, port, model)


@pytest.mark.parametrize("operation", ["tool", "model"])
def test_expired_context_does_not_start_a_readonly_tool_or_model(clock, operation):
    port = MemoryReadonly()
    model = InjectedModel()
    context = runtime.RunContext(port.run_id, port.incident.id, port, model,
                                 CoreSettings(task_timeout_seconds=5), clock=clock)
    context.routes = {route.task.role: route for route in select_agents(port.configs)}
    reads = []
    port.get_booking_rules = lambda: reads.append(True)
    clock.advance(5)
    with pytest.raises(CoreFailure, match="TASK_DEADLINE_EXCEEDED"):
        if operation == "tool":
            context.tool("impact", "get_booking_rules")
        else:
            context.model("impact", ImpactReport, {}, RunStatus.SCOPING)
    assert not reads and not model.requests and context.calls == 0


def test_one_consumed_model_request_returned_after_deadline_cannot_follow_tools(clock):
    port = MemoryReadonly()

    def late(result, payload, index):
        clock.advance(180)
        return {**result, "tools": [{"name": "get_booking_rules", "reservation_id": None}]}

    model = InjectedModel({"ImpactReport": late})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TASK_DEADLINE_EXCEEDED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 1
    assert not result.changes and not port.validation_calls
    assert not any(step.node == "core.tool_observation" for step in port.steps)
    completion = next(step for step in port.steps if step.node == "core.model")
    assert "input_tokens=3;output_tokens=2" in completion.output_digest


def test_valid_verifier_answer_returned_late_does_not_produce_a_plan(clock):
    port = MemoryReadonly()

    def late(result, payload, index):
        clock.advance(180)
        return result

    model = InjectedModel({"VerificationReport": late})
    original = port.snapshot.model_dump_json()
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TASK_DEADLINE_EXCEEDED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 3
    assert not result.changes and not any(step.node == "core.plan_ready" for step in port.steps)
    assert port.snapshot.model_dump_json() == original


def test_task_deadline_precedes_local_replan_allowance(clock):
    port = MemoryReadonly()
    append = port.append_run_step

    def expire_before_replan(step):
        recorded = append(step)
        if step.node == "core.plan_rejected":
            clock.advance(180)
        return recorded

    port.append_run_step = expire_before_replan
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "plan_id": "TEST_INJECTED_unoffered",
    }})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TASK_DEADLINE_EXCEEDED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 2
    assert not any(step.node == "core.replan" for step in port.steps)
    assert max(step.retry_count for step in port.steps) == 0


def test_task_timeout_default_and_private_configuration_are_finite_and_bounded():
    assert CoreSettings().task_timeout_seconds == 180
    assert CoreSettings.from_env().task_timeout_seconds == 180
    assert CoreSettings(task_timeout_seconds=0.5).task_timeout_seconds == 0.5


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "180", 3601])
def test_invalid_task_timeout_is_rejected(value):
    with pytest.raises(CoreFailure, match="INVALID_CONFIGURATION: task_timeout_seconds"):
        CoreSettings(task_timeout_seconds=value)


def test_expiry_during_start_audit_prevents_dispatch_and_counts_zero_attempts(clock):
    port = MemoryReadonly()
    append = port.append_run_step

    def expire_on_start(step):
        recorded = append(step)
        if step.node == "core.model_start":
            clock.advance(180)
        return recorded

    port.append_run_step = expire_on_start
    model = InjectedModel()
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TASK_DEADLINE_EXCEEDED"
    assert not model.requests and sum(step.model_calls for step in port.steps) == 0
    completion = next(step for step in port.steps if step.node == "core.model")
    assert completion.model_calls == 0 and completion.error.message == "TASK_DEADLINE_EXCEEDED"


def test_late_tool_result_is_not_projected_or_followed_by_another_model_request(clock):
    port = MemoryReadonly()
    original = port.get_booking_rules
    reads = []

    def late_requested_read():
        reads.append(True)
        if len(reads) == 3:  # scope, mandatory Impact read, then the model's requested read
            clock.advance(180)
        return original()

    port.get_booking_rules = late_requested_read
    model = InjectedModel({"ImpactReport": lambda result, payload, index: {
        **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}],
    }})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TASK_DEADLINE_EXCEEDED"
    assert len(reads) == 3 and len(model.requests) == sum(step.model_calls for step in port.steps) == 1
    assert not any(step.node == "core.tool_observation" for step in port.steps)
    assert any(step.node == "core.tool" and step.error and step.error.message == "TASK_DEADLINE_EXCEEDED"
               for step in port.steps)


def test_late_orchestration_read_stops_before_any_role_execution(clock):
    port = MemoryReadonly()

    def late_snapshot():
        clock.advance(180)
        return port.snapshot

    port.get_snapshot = late_snapshot
    model = InjectedModel()
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TASK_DEADLINE_EXCEEDED"
    assert not model.requests and sum(step.model_calls for step in port.steps) == 0


def test_one_deadline_spans_roles_and_the_observation_followup(clock):
    port = MemoryReadonly()

    def impact(result, payload, index):
        clock.advance(60)
        return result

    def planning(result, payload, index):
        clock.advance(60)
        return result if index else {
            **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}],
        }

    model = InjectedModel({"ImpactReport": impact, "PlanningDecision": planning})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TASK_DEADLINE_EXCEEDED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 3
    assert [request.agent_id for request in model.requests] == ["impact-agent", "planner-agent", "planner-agent"]
    assert len([step for step in port.steps if step.node == "core.tool_observation"]) == 1
    assert not result.changes and not port.validation_calls


def test_expiry_during_final_ready_audit_does_not_return_wait_approval(clock):
    port = MemoryReadonly()
    append = port.append_run_step

    def expire_on_ready(step):
        recorded = append(step)
        if step.node == "core.plan_ready":
            clock.advance(180)
        return recorded

    port.append_run_step = expire_on_ready
    model = InjectedModel()
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TASK_DEADLINE_EXCEEDED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 3
    assert not result.changes


def test_main_does_not_persist_a_proposal_from_a_late_valid_verifier(clock, main_factory):
    def late(result, payload, index):
        clock.advance(180)
        return result

    model = InjectedModel({"VerificationReport": late})
    service = main_factory(GearShiftOrchestrator(), model)
    incident = service.register_equipment_failure(IncidentCreate(
        equipment_id="cam-01", effective_at="2026-11-10T00:00:00Z", reason="TEST_INJECTED fictional deadline",
    ))
    original = service.list_reservations()
    result = service.start_workflow(incident.id)
    assert result.status == "FAILED" and result.error.message == "TASK_DEADLINE_EXCEEDED"
    assert result.proposal_id is None and not result.changes
    assert len(model.requests) == service.get_run(result.run_id).model_calls == 3
    assert service.list_reservations() == original
