"""Offline evidence protocol checks, not a replay of unavailable real responses."""

import json

import pytest

from campusops.agent_core import GearShiftOrchestrator
from campusops.contracts.models import ExecutionMode, IncidentCreate
from .support import InjectedModel, MemoryReadonly


def run(port, model):
    return GearShiftOrchestrator().run_incident(port.incident.id, port.run_id, port, model)


@pytest.mark.parametrize("corruption", [None, "version", "foreign_booking", "snapshot"])
def test_verifier_can_cite_actual_main_validator_versioned_evidence(main_factory, corruption):
    # Cite the real Main port's validation evidence, not guessed test-only IDs.
    def cite(result, payload, index):
        evidence = list(payload["validation"]["evidence_ids"])
        if corruption == "version":
            reference = next(item for item in evidence if item.startswith("reservation:") and ":v" in item)
            prefix, version = reference.rsplit(":v", 1)
            evidence[evidence.index(reference)] = prefix + ":v" + str(int(version) + 1)
        elif corruption == "foreign_booking":
            evidence.append("reservation:fictional_outside_scope:v1")
        elif corruption == "snapshot":
            reference = payload["evidence_id"]
            evidence[evidence.index(reference)] = reference[:-1] + ("0" if reference[-1] != "0" else "1")
        return {**result, "evidence_ids": evidence}
    model = InjectedModel({"VerificationReport": cite})
    mode = ExecutionMode.FAILURE_INJECTION_TEST if corruption else ExecutionMode.OFFLINE_MOCK_TEST
    service = main_factory(GearShiftOrchestrator(), model, mode)
    incident = service.register_equipment_failure(IncidentCreate(
        equipment_id="cam-01", effective_at="2026-11-10T00:00:00Z", reason="Fictional evidence compatibility check",
    ))
    original = service.list_reservations()
    result = service.start_workflow(incident.id)
    verifier = next(row for row in model.requests if json.loads(row.response_schema)["title"] == "VerificationReport")
    evidence = json.loads(verifier.messages[1].content)["validation"]["evidence_ids"]
    assert any(item.startswith("reservation:") and ":v" in item for item in evidence)
    assert result.status == ("NEEDS_MANUAL" if corruption else "WAIT_APPROVAL")
    if corruption:
        assert result.explanation == "INSUFFICIENT_VERIFIER_EVIDENCE" and result.proposal_id is None
    assert service.list_reservations() == original
    assert service.get_run(result.run_id).model_calls == 3


@pytest.mark.parametrize("title", ["ImpactReport", "VerificationReport"])
def test_three_booking_evidence_input_and_wire_schema_match_scope(title):
    port, model = MemoryReadonly(3), InjectedModel()
    assert run(port, model).status == "WAIT_APPROVAL"
    request = next(row for row in model.requests if json.loads(row.response_schema)["title"] == title)
    payload, schema = json.loads(request.messages[1].content), json.loads(request.response_schema)
    required = sorted(row.id for row in port.impacted)
    assert payload["required_reservation_ids"] == required
    assert payload["required_snapshot_evidence_id"] == payload["evidence_id"]
    allowed = payload["allowed_evidence_ids"]
    assert payload["evidence_id"] in allowed and "incident:" + port.incident.id in allowed
    assert all(f"reservation:{row.id}:v{row.version}" in allowed for row in port.impacted)
    identifiers = schema["properties"]["reservation_ids"]
    assert identifiers["items"]["enum"] == required
    assert identifiers["minItems"] == identifiers["maxItems"] == len(required)
    assert schema["properties"]["evidence_ids"]["items"]["enum"] == allowed


@pytest.mark.parametrize("title", ["ImpactReport", "VerificationReport"])
@pytest.mark.parametrize("mismatch", ["missing_snapshot", "foreign_reference", "stale_version", "missing_booking", "duplicate_booking"])
def test_invalid_metadata_is_not_filled_or_promoted_to_success(title, mismatch):
    port = MemoryReadonly(3)
    port.execution_mode = ExecutionMode.FAILURE_INJECTION_TEST
    original = port.snapshot.model_dump_json()
    def invalid(result, payload, index):
        if mismatch == "missing_snapshot":
            return {**result, "evidence_ids": ["incident:" + port.incident.id]}
        if mismatch == "foreign_reference":
            return {**result, "evidence_ids": result["evidence_ids"] + ["reservation:foreign:v1"]}
        if mismatch == "stale_version":
            row = port.impacted[0]
            return {**result, "evidence_ids": result["evidence_ids"] + [f"reservation:{row.id}:v{row.version + 1}"]}
        if mismatch == "missing_booking":
            return {**result, "reservation_ids": result["reservation_ids"][:-1]}
        return {**result, "reservation_ids": [port.impacted[0].id] * len(port.impacted)}
    model = InjectedModel({title: invalid})
    result = run(port, model)
    assert result.status == "NEEDS_MANUAL" and not result.changes
    assert result.explanation == ("INSUFFICIENT_IMPACT_EVIDENCE" if title == "ImpactReport" else "INSUFFICIENT_VERIFIER_EVIDENCE")
    assert len(model.requests) == (1 if title == "ImpactReport" else 3)
    assert max(row.retry_count for row in port.steps) == 0
    assert port.snapshot.model_dump_json() == original


def test_model_manual_verdict_is_preserved_with_complete_metadata():
    port = MemoryReadonly(3)
    model = InjectedModel({"VerificationReport": lambda result, payload, index: {
        **result, "verdict": "needs_manual", "reason_codes": ["manual_review"],
    }})
    result = run(port, model)
    assert result.status == "NEEDS_MANUAL" and result.explanation == "VERIFIER_REQUESTED_MANUAL_REVIEW"
    assert len(model.requests) == 3 and not result.changes
