"""Offline graph and fault-injection evidence; never real-model acceptance."""

import json
import threading

import pytest

from campusops.agent_core import CoreSettings, GearShiftOrchestrator
from campusops.agent_core.runtime import RunContext
from campusops.agent_core.errors import CoreFailure
from campusops.contracts.models import ErrorCode, ExecutionMode, ValidationIssue, ValidationResult
from campusops.contracts.ports import ModelClientPort, OrchestrationPort, ReadonlyPort
from .support import InjectedModel, MemoryReadonly


def run(port, model, **limits):
    return GearShiftOrchestrator(CoreSettings(**limits)).run_incident(port.incident.id, port.run_id, port, model)


def test_success_is_three_independent_roles_and_no_booking_mutation(port, model):
    before = port.snapshot.model_dump_json()
    result = run(port, model)
    assert result.status == "WAIT_APPROVAL" and result.proposal_id is None
    assert len(result.changes) == 1 and result.validation.passed
    assert [json.loads(row.response_schema)["title"] for row in model.requests] == [
        "ImpactReport", "PlanningDecision", "VerificationReport"]
    assert len({row.messages[0].content for row in model.requests}) == 3
    assert len(port.validation_calls) == 2
    assert sum(row.model_calls for row in port.steps) == 3
    assert all(row.execution_mode == ExecutionMode.OFFLINE_MOCK_TEST for row in port.steps)
    assert before == port.snapshot.model_dump_json()
    assert isinstance(GearShiftOrchestrator(), OrchestrationPort)
    assert isinstance(port, ReadonlyPort) and isinstance(model, ModelClientPort)
    assert "borrower_alias" not in str([row.messages for row in model.requests])
    assert port.incident.reason not in str([row.messages for row in model.requests])


def test_graph_has_conditional_fanout_and_planning_only_feedback(port, model):
    graph = GearShiftOrchestrator()._build_graph(RunContext(port.run_id, port.incident.id, port, model, CoreSettings()))
    edges = {(row.source, row.target) for row in graph.get_graph().edges}
    assert {("impact", "query_candidates"), ("query_candidates", "solve"),
            ("planner", "replan"), ("verifier", "replan"), ("replan", "planner")} <= edges
    assert ("replan", "impact") not in edges


def test_no_impact_never_invokes_model_or_registry():
    port = MemoryReadonly(count=0)
    port.list_agent_configs = lambda: pytest.fail("NO_IMPACT must not route agents")
    model = InjectedModel()
    result = run(port, model)
    assert result.status == "NO_IMPACT" and result.validation.passed
    assert model.requests == [] and not port.validation_calls


def test_three_queries_really_overlap_and_gather_once():
    port = MemoryReadonly(count=3)
    port.barrier = threading.Barrier(3)
    model = InjectedModel()
    result = run(port, model)
    assert result.status == "WAIT_APPROVAL"
    assert port.max_active == 3
    assert len(result.changes) == 3
    assert len([row for row in port.steps if row.node == "core.solve"]) == 1
    assert any("parallel_fan_out;tasks=3" in row.decision_reason for row in port.steps)
    assert {row.reservation_id for row in result.changes} == {row.id for row in port.impacted}


@pytest.mark.parametrize("patch", [
    {"enabled": False}, {"skills": ("unrelated",)}, {"allowed_tools": ("append_run_step",)},
    {"allowed_tools": ("shell",)},
])
def test_missing_capability_or_permission_blocks_before_calls(port, model, patch):
    port.configs = tuple(row.model_copy(update=patch) if row.role == "planner" else row for row in port.configs)
    result = run(port, model)
    assert result.status == "NEEDS_MANUAL" and not model.requests


def test_disabled_agent_selects_real_alternate_and_prompt_version(port, model):
    original = next(row for row in port.configs if row.role == "planner")
    port.configs += (original.model_copy(update={"agent_id": "planner-alternate", "prompt_version": "v2"}),)
    port.configs = tuple(row.model_copy(update={"enabled": False}) if row.agent_id == original.agent_id else row
                         for row in port.configs)
    result = run(port, model)
    assert result.status == "WAIT_APPROVAL"
    assert model.requests[1].agent_id == "planner-alternate"
    assert "Prompt version: v2" in model.requests[1].messages[0].content
    assert any(row.node == "core.route" and row.agent_id == "planner-alternate" for row in port.steps)


@pytest.mark.parametrize("tool", [
    {"name": "execute_sql", "reservation_id": None},
    {"name": "list_equipment_candidates", "reservation_id": "outside_scope"},
])
def test_model_tool_requests_cannot_escape_whitelist_or_scope(port, tool):
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {**result, "tools": [tool]}})
    result = run(port, model)
    assert result.status == "FAILED" and result.error.code == ErrorCode.INVALID_INPUT
    assert not result.changes and not port.validation_calls
    assert any(row.node == "core.tool_rejected" for row in port.steps)


def test_impact_role_cannot_borrow_planner_tool(port):
    model = InjectedModel({"ImpactReport": lambda result, payload, index: {
        **result, "tools": [{"name": "list_equipment_candidates", "reservation_id": port.impacted[0].id}],
    }})
    result = run(port, model)
    assert result.error.message == "TOOL_PERMISSION_DENIED"
    assert len(model.requests) == 1


def test_allowed_tool_and_audit_marker_are_actual_events(port):
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "tools": [{"name": "get_booking_rules", "reservation_id": None},
                            {"name": "append_run_step", "reservation_id": None}] if index == 0 else [],
    }})
    assert run(port, model).status == "WAIT_APPROVAL"
    markers = [row for row in port.steps if row.tool_name == "append_run_step"]
    assert len(markers) == 1 and markers[0].model_calls == 0 and markers[0].status is None
    assert len(model.requests) == 4
    assert json.loads(model.requests[2].messages[-1].content)["tool_observations"]


def test_fault_injected_unoffered_plan_gets_feedback_then_success(port):
    port.execution_mode = ExecutionMode.FAILURE_INJECTION_TEST
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "plan_id": "invented_plan" if index == 0 else result["plan_id"],
    }})
    result = run(port, model)
    assert result.status == "WAIT_APPROVAL"
    planners = [row for row in model.requests if json.loads(row.response_schema)["title"] == "PlanningDecision"]
    assert len(planners) == 2 and json.loads(planners[1].messages[1].content)["feedback"]
    assert len(model.requests) == 4 and max(row.retry_count for row in port.steps) == 1
    assert all(row.execution_mode == ExecutionMode.FAILURE_INJECTION_TEST for row in port.steps)


def test_repeated_unoffered_plan_stops_at_two_replans(port):
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {**result, "plan_id": "invented"}})
    result = run(port, model)
    assert result.status == "NEEDS_MANUAL" and result.explanation == "REPLAN_LIMIT_REACHED"
    assert len(model.requests) == 4 and max(row.retry_count for row in port.steps) == 2
    assert not port.validation_calls


@pytest.mark.parametrize("pass_after,expected", [(2, "WAIT_APPROVAL"), (99, "NEEDS_MANUAL")])
def test_independent_verifier_feedback_is_bounded(port, pass_after, expected):
    model = InjectedModel({"VerificationReport": lambda result, payload, index: {
        **result, "verdict": "pass" if index >= pass_after else "revise",
        "reason_codes": ["constraints_satisfied" if index >= pass_after else "explanation_incomplete"],
    }})
    result = run(port, model, calls_per_run=10)
    assert result.status == expected and len(model.requests) == 7
    assert max(row.retry_count for row in port.steps) == 2
    assert model.counts["ImpactReport"] == 1


def test_default_call_budget_takes_precedence_over_final_verifier(port):
    model = InjectedModel({"VerificationReport": lambda result, payload, index: {**result, "verdict": "revise"}})
    result = run(port, model)
    assert result.status == "FAILED" and result.error.code == ErrorCode.BUDGET_EXCEEDED
    assert len(model.requests) == 6 and sum(row.model_calls for row in port.steps) == 6


def test_main_hard_validation_rejection_never_reaches_verifier(port, model):
    port.validation = ValidationResult(passed=False, issues=("TEST_INJECTED constraint rejection",))
    result = run(port, model)
    assert result.status == "NEEDS_MANUAL" and not result.changes
    assert "VerificationReport" not in model.counts
    assert len(port.validation_calls) == 3


@pytest.mark.parametrize("title", ["ImpactReport", "VerificationReport"])
def test_invented_evidence_goes_to_manual(port, title):
    model = InjectedModel({title: lambda result, payload, index: {**result, "evidence_ids": ["invented"]}})
    result = run(port, model)
    assert result.status == "NEEDS_MANUAL" and "EVIDENCE" in result.explanation


def test_malformed_output_and_provider_error_are_redacted(port):
    secret = "TEST_SECRET_DO_NOT_PERSIST"
    for response in (secret, RuntimeError(secret)):
        fresh = MemoryReadonly()
        model = InjectedModel({"ImpactReport": lambda result, payload, index: response})
        result = run(fresh, model)
        assert result.status == "FAILED"
        assert secret not in result.model_dump_json() + str([row.model_dump_json() for row in fresh.steps])
        assert sum(row.model_calls for row in fresh.steps) == 1


def test_no_model_client_is_unconfigured(port):
    result = run(port, None)
    assert result.error.code == ErrorCode.PROVIDER_UNAVAILABLE and "UNCONFIGURED" in result.error.message
    assert sum(row.model_calls for row in port.steps) == 0


def test_daily_usage_floor_blocks_before_model(port, model):
    port.daily_calls = 30
    result = run(port, model)
    assert result.error.code == ErrorCode.BUDGET_EXCEEDED and not model.requests


def test_no_candidate_and_search_limit_are_distinct(port, model):
    port.candidates = {key: () for key in port.candidates}
    assert run(port, model).explanation == "NO_FEASIBLE_PLAN"
    limited = run(MemoryReadonly(), InjectedModel(), search_nodes=1)
    assert limited.status == "NEEDS_MANUAL" and limited.explanation == "SEARCH_LIMIT_REACHED_NO_PLAN_PROVEN"


def test_registry_change_during_run_affects_only_a_new_run(port):
    def disable(result, payload, index):
        port.configs = tuple(row.model_copy(update={"enabled": False}) if row.role == "planner" else row for row in port.configs)
        return result
    model = InjectedModel({"ImpactReport": disable})
    result = run(port, model)
    assert result.status == "WAIT_APPROVAL" and len(model.requests) == 3
    next_port = MemoryReadonly(run_id="run_after_registry_change")
    next_port.configs = port.configs
    next_model = InjectedModel()
    assert run(next_port, next_model).status == "NEEDS_MANUAL" and not next_model.requests


def test_repeat_runs_keep_counts_and_logs_separate():
    core = GearShiftOrchestrator()
    model = InjectedModel()
    first, second = MemoryReadonly(run_id="run_first"), MemoryReadonly(run_id="run_second")
    for port in (first, second):
        assert core.run_incident(port.incident.id, port.run_id, port, model).status == "WAIT_APPROVAL"
        assert sum(row.model_calls for row in port.steps) == 3
        assert [row.seq for row in port.steps] == list(range(1, len(port.steps) + 1))
        assert {row.run_id for row in port.steps} == {port.run_id}


def test_failed_audit_never_returns_plan(port, model):
    def denied(step):
        raise RuntimeError("TEST_SECRET_DO_NOT_PERSIST")
    port.append_run_step = denied
    result = run(port, model)
    assert result.status == "FAILED" and result.error.message == "AUDIT_APPEND_FAILED"
    assert not model.requests


def test_verifier_cannot_pass_with_contradictory_reason_codes(port):
    model = InjectedModel({"VerificationReport": lambda result, payload, index: {
        **result, "verdict": "pass", "reason_codes": ["candidate_mismatch"],
    }})
    result = run(port, model)
    assert result.status == "FAILED" and result.error.message == "MODEL_STRUCTURED_OUTPUT_INVALID"


def test_pre_dispatch_budget_denial_is_not_logged_as_a_model_attempt(port):
    class BlockedInjectedClient:
        def complete(self, request):
            raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "TEST_INJECTED_PRE_DISPATCH_BUDGET_DENIAL")
    result = run(port, BlockedInjectedClient())
    assert result.status == "FAILED" and result.error.code == ErrorCode.BUDGET_EXCEEDED
    assert sum(row.model_calls for row in port.steps) == 0


@pytest.mark.parametrize("maximum", [0, 1])
def test_lower_replan_configuration_really_limits_execution(port, maximum):
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {**result, "plan_id": "invented"}})
    assert run(port, model, max_replans=maximum).status == "NEEDS_MANUAL"
    assert len(model.requests) == maximum + 2
    assert max(row.retry_count for row in port.steps) == maximum


def test_snapshot_conflict_requires_new_main_run_without_replanning(port, model):
    port.validation = ValidationResult(passed=False, violations=(
        ValidationIssue(code=ErrorCode.SNAPSHOT_CONFLICT, message="TEST_INJECTED stale snapshot"),
    ))
    result = run(port, model)
    assert result.status == "NEEDS_MANUAL" and result.explanation == "SNAPSHOT_CHANGED_RESTART_REQUIRED"
    assert len(model.requests) == 2 and max(row.retry_count for row in port.steps) == 0


def test_one_worker_records_serial_fanout_honestly():
    port = MemoryReadonly(3)
    assert run(port, InjectedModel(), parallel_queries=1).status == "WAIT_APPROVAL"
    assert any("serial_fan_out;tasks=3" in row.decision_reason for row in port.steps)
