"""Offline Core acceptance against Main's public factory and fictional CLI seed.

These are isolated composition checks on this branch, not Main merge acceptance.
"""

import json

import pytest

from campusops.agent_core import GearShiftOrchestrator
from campusops.contracts.models import AgentConfigPatch, AgentRuntimeConfigPatch, ExecutionMode, IncidentCreate
from .support import InjectedModel


def fault(service):
    return service.register_equipment_failure(IncidentCreate(
        equipment_id="cam-01", effective_at="2026-11-10T00:00:00Z", reason="Fictional Core composition fault",
    ))


def test_main_persists_proposal_and_test_logs_without_changing_bookings(main_factory, model):
    service = main_factory(GearShiftOrchestrator(), model)
    incident = fault(service)
    original = service.list_reservations()
    result = service.start_workflow(incident.id)
    assert result.status == "WAIT_APPROVAL" and result.proposal_id
    assert service.list_reservations() == original
    run = service.get_run(result.run_id)
    assert run.status == "WAIT_APPROVAL" and run.model_calls == 3
    steps = service.list_run_steps(result.run_id)
    assert all(row.execution_mode == ExecutionMode.OFFLINE_MOCK_TEST for row in steps)
    assert sum(row.model_calls for row in steps) == 3
    proposal = service.get_proposal(result.proposal_id)
    assert proposal.validation.passed and proposal.changes == result.changes
    # Only Main's public approval service writes the fictional reservations.
    applied = service.approve_proposal(result.proposal_id, "Fictional-admin", "core_test_approval")
    assert applied.status == "APPLIED"
    assert service.approve_proposal(result.proposal_id, "Fictional-admin", "core_test_approval") == applied


def test_main_registry_patch_changes_subsequent_routing(main_factory, model):
    service = main_factory(GearShiftOrchestrator(), model)
    incident = fault(service)
    service.update_agent_config("planner-agent", AgentConfigPatch(enabled=False))
    blocked = service.start_workflow(incident.id)
    assert blocked.status == "NEEDS_MANUAL" and not model.requests
    service.update_agent_config("planner-agent", AgentConfigPatch(enabled=True, prompt_version="v2"))
    ready = service.start_workflow(incident.id)
    assert ready.status == "WAIT_APPROVAL" and "Prompt version: v2" in model.requests[1].messages[0].content


def test_mid_run_configuration_is_frozen_and_disable_affects_next_run(main_factory):
    def revoke(result, payload, index):
        service.update_agent_config("impact-agent", AgentConfigPatch(enabled=False))
        return result
    model = InjectedModel({"ImpactReport": revoke})
    service = main_factory(GearShiftOrchestrator(), model)
    incident = fault(service)
    original = service.list_reservations()
    result = service.start_workflow(incident.id)
    assert result.status == "WAIT_APPROVAL" and result.changes
    assert len(model.requests) == service.get_run(result.run_id).model_calls == 3
    assert service.list_reservations() == original
    blocked = service.start_workflow(incident.id)
    assert blocked.status == "NEEDS_MANUAL" and not blocked.changes
    assert service.get_run(blocked.run_id).model_calls == 0
    assert len(model.requests) == 3


def test_main_freezes_prompt_and_tools_while_new_run_uses_the_updated_safe_subset(main_factory):
    def update(result, payload, index):
        if index == 0:
            service.update_agent_config("planner-agent", AgentRuntimeConfigPatch(
                prompt_version="v2", allowed_tools=("list_equipment_candidates", "validate_candidate_plan"),
            ))
        return result

    def marker_if_allowed(result, payload, index):
        if "append_run_step" in payload["allowed_tools"] and not payload.get("tool_observations"):
            return {**result, "tools": [{"name": "append_run_step", "reservation_id": None}]}
        return result

    model = InjectedModel({"ImpactReport": update, "PlanningDecision": marker_if_allowed})
    service = main_factory(GearShiftOrchestrator(), model)
    incident = fault(service)
    original = service.list_reservations()
    first = service.start_workflow(incident.id)
    assert first.status == "WAIT_APPROVAL" and service.get_run(first.run_id).model_calls == 4
    first_planner = model.requests[1]
    assert "Prompt version: v1" in first_planner.messages[0].content
    assert "append_run_step" in json.loads(first_planner.messages[1].content)["allowed_tools"]
    assert len([step for step in service.list_run_steps(first.run_id) if step.tool_name == "append_run_step"]) == 1
    second = service.start_workflow(incident.id)
    assert second.status == "WAIT_APPROVAL" and service.get_run(second.run_id).model_calls == 3
    second_planner = model.requests[5]
    assert "Prompt version: v2" in second_planner.messages[0].content
    assert json.loads(second_planner.messages[1].content)["allowed_tools"] == [
        "list_equipment_candidates", "validate_candidate_plan",
    ]
    assert not any(step.tool_name == "append_run_step" for step in service.list_run_steps(second.run_id))
    assert service.list_reservations() == original


def test_main_actual_fictional_rule_catalog_is_consumed_in_a_model_followup(main_factory):
    observed_catalogs = []

    def consume_rules(result, payload, index):
        if not payload.get("tool_observations"):
            return {**result, "tools": [{"name": "get_booking_rules", "reservation_id": None}]}
        catalog = payload["tool_observations"][0]["result"]
        assert catalog == payload["rules"]
        assert catalog["fictional"] is True and len(catalog["fingerprint"]) == 64
        assert catalog["ruleset_id"] and catalog["version"] and catalog["source"]
        assert catalog["rules"] and all(row["rule_id"] and row["version"] and row["content"] and row["source"]
                                        and row["fictional"] is True for row in catalog["rules"])
        observed_catalogs.append(catalog)
        return result

    model = InjectedModel({"PlanningDecision": consume_rules})
    service = main_factory(GearShiftOrchestrator(), model)
    incident = fault(service)
    original = service.list_reservations()
    result = service.start_workflow(incident.id)
    assert result.status == "WAIT_APPROVAL" and len(observed_catalogs) == 1
    assert len(model.requests) == service.get_run(result.run_id).model_calls == 4
    assert service.list_reservations() == original
    assert all(step.execution_mode == ExecutionMode.OFFLINE_MOCK_TEST
               for step in service.list_run_steps(result.run_id))


@pytest.mark.parametrize("title", ["ImpactReport", "VerificationReport"])
@pytest.mark.parametrize("wrong_version", [False, True])
def test_actual_main_rule_references_are_allowed_only_at_the_frozen_catalog_version(main_factory, title, wrong_version):
    def cite_rule(result, payload, index):
        catalog = payload["rules"]
        rule = catalog["rules"][0]
        reference = f"rule:{rule['rule_id']}:v{rule['version']}"
        assert reference in payload["allowed_evidence_ids"]
        assert f"ruleset:{catalog['ruleset_id']}:v{catalog['version']}" in payload["allowed_evidence_ids"]
        return {**result, "evidence_ids": result["evidence_ids"] + [
            reference + "-TEST_INJECTED_WRONG_VERSION" if wrong_version else reference,
        ]}

    model = InjectedModel({title: cite_rule})
    service = main_factory(GearShiftOrchestrator(), model)
    incident = fault(service)
    original = service.list_reservations()
    result = service.start_workflow(incident.id)
    assert result.status == ("NEEDS_MANUAL" if wrong_version else "WAIT_APPROVAL")
    assert service.list_reservations() == original
    if wrong_version:
        assert not result.changes and result.proposal_id is None


def test_approval_conflict_is_rechecked_by_main(main_factory, model):
    service = main_factory(GearShiftOrchestrator(), model)
    result = service.start_workflow(fault(service).id)
    assert result.status == "WAIT_APPROVAL"
    original = service.list_reservations()
    service.register_equipment_failure(IncidentCreate(
        equipment_id=result.changes[0].to_equipment_id,
        effective_at="2026-11-10T00:00:00Z", reason="Fictional approval conflict",
    ))
    applied = service.approve_proposal(result.proposal_id, "Fictional-admin", "conflict_test")
    assert applied.status == "STALE" and service.list_reservations() == original
