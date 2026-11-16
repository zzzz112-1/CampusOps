"""Offline permission-context probes; never a reconstruction of real output."""

import json

import pytest

from campusops.agent_core import GearShiftOrchestrator
from campusops.agent_core.roles import ImpactReport
from campusops.contracts.models import ExecutionMode
from .support import InjectedModel, MemoryReadonly


def execute(port=None, model=None):
    port = port or MemoryReadonly(count=3)
    model = model or InjectedModel()
    result = GearShiftOrchestrator().run_incident(port.incident.id, port.run_id, port, model)
    return result, port, model


def tool_branches(request):
    definition = json.loads(request.response_schema)["$defs"]["ToolRequest"]
    return definition.get("anyOf", [definition])


def tool_names(request):
    return sorted(name for branch in tool_branches(request) for name in branch["properties"]["name"].get("enum", []))


def test_three_booking_impact_prompt_does_not_advertise_planner_only_tools():
    result, port, model = execute()
    assert result.status == "WAIT_APPROVAL"
    impact = model.requests[0]
    prompt = " ".join(message.content for message in impact.messages)
    assert "list_equipment_candidates" not in prompt
    assert "validate_candidate_plan" not in prompt


def test_three_roles_wire_schema_matches_each_selected_registry_allowlist():
    result, port, model = execute()
    assert result.status == "WAIT_APPROVAL"
    configs = {row.agent_id: row for row in port.configs}
    for request in model.requests:
        actual = sorted(configs[request.agent_id].allowed_tools)
        assert tool_names(request) == actual
        payload = json.loads(request.messages[1].content)
        assert payload["allowed_tools"] == actual


def test_allowed_tool_contracts_describe_exact_bound_arguments():
    result, port, model = execute()
    assert result.status == "WAIT_APPROVAL"
    for request in model.requests:
        payload = json.loads(request.messages[1].content)
        contracts = payload["tool_contracts"]
        assert sorted(contracts) == payload["allowed_tools"]
        for name, contract in contracts.items():
            assert set(contract["arguments"]) == {"reservation_id"}
            argument = contract["arguments"]["reservation_id"]
            if name == "list_equipment_candidates":
                assert argument["type"] == "string"
                assert argument["enum"] == sorted(row.id for row in port.impacted)
            else:
                assert argument == {"type": "null"}
        for branch in tool_branches(request):
            assert branch["additionalProperties"] is False
            assert set(branch["required"]) == {"name", "reservation_id"}
            names = branch["properties"]["name"]["enum"]
            argument = branch["properties"]["reservation_id"]
            if "list_equipment_candidates" in names:
                assert names == ["list_equipment_candidates"] and argument["type"] == "string"
                assert argument["enum"] == sorted(row.id for row in port.impacted)
            else:
                assert argument == {"type": "null"}


def test_tool_context_changes_with_selected_permissions_without_global_schema_mutation():
    original_schema = ImpactReport.model_json_schema()
    first, _, original_model = execute()
    port = MemoryReadonly(count=3)
    port.configs = tuple(row.model_copy(update={
        "allowed_tools": tuple(name for name in row.allowed_tools if name != "append_run_step"),
    }) if row.role == "impact" else row for row in port.configs)
    second, _, limited_model = execute(port)
    assert first.status == second.status == "WAIT_APPROVAL"
    assert "append_run_step" in tool_names(original_model.requests[0])
    assert "append_run_step" not in tool_names(limited_model.requests[0])
    assert ImpactReport.model_json_schema() == original_schema


def test_published_role_tool_limit_cannot_be_overridden_by_a_registry_entry():
    port = MemoryReadonly()
    port.configs = tuple(row.model_copy(update={
        "allowed_tools": row.allowed_tools + ("list_equipment_candidates",),
    }) if row.role == "impact" else row for row in port.configs)
    model = InjectedModel()
    result, _, _ = execute(port, model)
    assert result.status == "NEEDS_MANUAL" and not model.requests


@pytest.mark.parametrize("name", ["list_equipment_candidates", "query_candidates", "update_reservation"])
def test_synthetic_illegal_impact_request_still_stops_with_one_call(name):
    # The real rejected tool name is UNKNOWN. This is a separate permission probe.
    port = MemoryReadonly(count=3)
    port.execution_mode = ExecutionMode.FAILURE_INJECTION_TEST
    before = port.snapshot.model_dump_json()
    model = InjectedModel({"ImpactReport": lambda result, payload, index: {
        **result, "tools": [{"name": name, "reservation_id": None}],
    }})
    result, _, _ = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TOOL_PERMISSION_DENIED"
    assert len(model.requests) == sum(row.model_calls for row in port.steps) == 1
    assert max(row.retry_count for row in port.steps) == 0
    assert not port.validation_calls and not port._seen
    assert port.snapshot.model_dump_json() == before


def test_non_candidate_argument_scope_remains_enforced():
    port = MemoryReadonly(count=3)
    model = InjectedModel({"ImpactReport": lambda result, payload, index: {
        **result, "tools": [{"name": "get_booking_rules", "reservation_id": port.impacted[0].id}],
    }})
    result, _, _ = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TOOL_ARGUMENTS_OUT_OF_SCOPE"
    assert len(model.requests) == 1


def test_authorized_role_tool_requests_remain_executable():
    port = MemoryReadonly(count=3)
    model = InjectedModel({
        "ImpactReport": lambda result, payload, index: {
            **result, "tools": [{"name": "get_impacted_reservations", "reservation_id": None}] if index == 0 else [],
        },
        "PlanningDecision": lambda result, payload, index: {
            **result, "tools": [{"name": "list_equipment_candidates", "reservation_id": port.impacted[0].id},
                                 {"name": "validate_candidate_plan", "reservation_id": None}] if index == 0 else [],
        },
        "VerificationReport": lambda result, payload, index: {
            **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}] if index == 0 else [],
        },
    })
    result, _, _ = execute(port, model)
    assert result.status == "WAIT_APPROVAL" and len(model.requests) == 6
    assert len(port.validation_calls) == 3
    assert all(json.loads(request.messages[-1].content)["tool_observations"]
               for request in model.requests if len(request.messages) > 2)
