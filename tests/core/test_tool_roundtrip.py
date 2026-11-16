"""TEST_INJECTED tool/observation conversations; no real model or network."""

import json

import pytest

from campusops.agent_core import CoreSettings, GearShiftOrchestrator
from campusops.contracts.models import BookingRules, ValidationIssue, ValidationResult
from .support import InjectedModel, MemoryReadonly


def execute(port, model, **limits):
    return GearShiftOrchestrator(CoreSettings(**limits)).run_incident(
        port.incident.id, port.run_id, port, model,
    )


def test_tool_observation_changes_final_choice_instead_of_reusing_initial_answer():
    port = MemoryReadonly()

    def choose(result, payload, index):
        if index == 0:
            return {**result, "tools": [{
                "name": "list_equipment_candidates", "reservation_id": port.impacted[0].id,
            }]}
        observations = payload["tool_observations"]
        candidates = observations[0]["result"]
        assert any(row["id"] == "camera_b" for row in candidates)
        selected = next(plan for plan in payload["offered_plans"]
                        if plan["changes"][0]["to_equipment_id"] == "camera_b")
        return {**result, "plan_id": selected["plan_id"]}

    model = InjectedModel({"PlanningDecision": choose})
    original = port.snapshot.model_dump_json()
    result = execute(port, model)
    assert result.status == "WAIT_APPROVAL"
    assert result.changes[0].to_equipment_id == "camera_b"
    assert all(items[0].to_equipment_id == "camera_b" for items in port.validation_calls)
    assert [request.agent_id for request in model.requests] == [
        "impact-agent", "planner-agent", "planner-agent", "verifier-agent",
    ]
    followup = model.requests[2]
    assert [message.role for message in followup.messages] == ["system", "user", "assistant", "user"]
    observation = json.loads(followup.messages[-1].content)["tool_observations"][0]
    parent = next(step for step in port.steps if step.node == "core.model_start" and step.agent_id == "planner-agent")
    assert observation["request_digest"] == parent.input_digest
    assert observation["run_id"] == port.run_id and observation["role"] == "planner"
    assert observation["agent_id"] == "planner-agent"
    assert observation["source"]["snapshot_fingerprint"] == port.snapshot.fingerprint
    assert observation["source"]["method"] == "list_equipment_candidates"
    assert observation["observation_id"] in followup.messages[-1].content
    assert observation["tool_request_digest"].startswith("sha256:")
    assert any(step.node == "core.tool_observation" for step in port.steps)
    assert sum(step.model_calls for step in port.steps) == 4
    assert max(step.retry_count for step in port.steps) == 0
    assert port.snapshot.model_dump_json() == original


@pytest.mark.parametrize("failure", ["invalid-json", "provider-error"])
def test_missing_valid_post_observation_answer_never_reuses_initial_plan(failure):
    port = MemoryReadonly()

    def fail_after_tool(result, payload, index):
        if index == 0:
            return {**result, "tools": [{"name": "get_booking_rules", "reservation_id": None}]}
        return "{invalid-json" if failure == "invalid-json" else RuntimeError("TEST_INJECTED_PRIVATE_ERROR")

    model = InjectedModel({"PlanningDecision": fail_after_tool})
    result = execute(port, model)
    assert result.status == "FAILED" and not result.changes
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 3
    assert not port.validation_calls
    assert all(request.agent_id != "verifier-agent" for request in model.requests)
    assert "TEST_INJECTED_PRIVATE_ERROR" not in result.model_dump_json() + str(port.steps)


def test_tool_followup_uses_same_run_budget_without_hidden_retry():
    port = MemoryReadonly()
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}],
    }})
    result = execute(port, model, calls_per_run=2)
    assert result.status == "FAILED" and result.error.code == "BUDGET_EXCEEDED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 2
    assert not result.changes and not port.validation_calls
    assert any(step.node == "core.tool_observation" for step in port.steps)


def test_second_tool_batch_stops_before_a_third_model_decision_or_any_plan_validation():
    port = MemoryReadonly()
    model = None

    def consume(result, payload, index):
        assert index < 2, "A third model decision must never be invoked"
        if index == 0:
            return {**result, "tools": [{"name": "get_booking_rules", "reservation_id": None}]}
        messages = model.requests[-1].messages
        observations = [json.loads(message.content)["tool_observations"][0]
                        for message in messages[3::2]]
        assert [row["tool_name"] for row in observations] == ["get_booking_rules"]
        assert observations[0]["result"]["preserve_original_times"] is True
        return {**result, "plan_id": payload["offered_plans"][-1]["plan_id"],
                "tools": [{"name": "get_incident", "reservation_id": None}]}

    model = InjectedModel({"PlanningDecision": consume})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TOOL_ROUND_LIMIT_REACHED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 3
    assert len(model.requests[2].messages) == 4
    assert not result.changes and not port.validation_calls
    assert max(step.retry_count for step in port.steps) == 0


@pytest.mark.parametrize("title", ["ImpactReport", "VerificationReport"])
def test_final_post_observation_evidence_or_verdict_controls_the_workflow(title):
    port = MemoryReadonly()

    def reconsider(result, payload, index):
        if index == 0:
            return {**result, "tools": [{"name": "get_booking_rules", "reservation_id": None}]}
        if title == "ImpactReport":
            return {**result, "reservation_ids": []}
        return {**result, "verdict": "needs_manual", "reason_codes": ["manual_review"]}

    model = InjectedModel({title: reconsider})
    result = execute(port, model)
    assert result.status == "NEEDS_MANUAL" and not result.changes
    assert len(model.requests) == (2 if title == "ImpactReport" else 4)
    if title == "ImpactReport":
        assert not port._seen and not port.validation_calls
    else:
        assert result.explanation == "VERIFIER_REQUESTED_MANUAL_REVIEW"


def test_validation_observation_codes_change_selection_without_forwarding_free_error_text():
    port = MemoryReadonly()
    private = "TEST_INJECTED_PRIVATE_VALIDATION_MESSAGE"

    def validate(changes):
        port.validation_calls.append(changes)
        if len(port.validation_calls) == 1:
            return ValidationResult(passed=False, issues=(private,), violations=(ValidationIssue(
                code="CAPABILITY_MISMATCH", message=private, entity_id=port.impacted[0].id,
            ),))
        return port.validation

    port.validate_candidate_plan = validate

    def reconsider(result, payload, index):
        if index == 0:
            return {**result, "tools": [{"name": "validate_candidate_plan", "reservation_id": None}]}
        validation = payload["tool_observations"][0]["result"]
        assert validation["passed"] is False
        assert validation["violations"] == [{"code": "CAPABILITY_MISMATCH", "entity_id": port.impacted[0].id}]
        return {**result, "plan_id": payload["offered_plans"][-1]["plan_id"]}

    model = InjectedModel({"PlanningDecision": reconsider})
    result = execute(port, model)
    assert result.status == "WAIT_APPROVAL" and result.changes[0].to_equipment_id == "camera_b"
    assert [items[0].to_equipment_id for items in port.validation_calls] == ["camera_a", "camera_b", "camera_b"]
    messages = " ".join(message.content for request in model.requests for message in request.messages)
    assert private not in messages and private not in str(port.steps)


@pytest.mark.parametrize("title,name", [
    ("ImpactReport", "get_incident"),
    ("ImpactReport", "get_impacted_reservations"),
    ("ImpactReport", "get_booking_rules"),
    ("ImpactReport", "append_run_step"),
    ("PlanningDecision", "list_equipment_candidates"),
    ("PlanningDecision", "validate_candidate_plan"),
])
def test_each_tool_returns_structured_observations_without_private_record_fields(title, name):
    port = MemoryReadonly()
    port.incident = port.incident.model_copy(update={"reason": "TEST_INJECTED_PRIVATE_REASON"})
    port.impacted = tuple(row.model_copy(update={"borrower_alias": "TEST_INJECTED_PRIVATE_ALIAS"})
                          for row in port.impacted)
    port.equipment = tuple(row.model_copy(update={"name": "TEST_INJECTED_PRIVATE_NAME"})
                           for row in port.equipment)
    port.snapshot = port.snapshot.model_copy(update={
        "incidents": (port.incident,), "reservations": port.impacted, "equipment": port.equipment,
    })
    port.candidates = {row.id: port.equipment[1:] for row in port.impacted}

    def request_once(result, payload, index):
        if not payload.get("tool_observations"):
            return {**result, "tools": [{
                "name": name, "reservation_id": port.impacted[0].id if name == "list_equipment_candidates" else None,
            }]}
        return result

    model = InjectedModel({title: request_once})
    assert execute(port, model).status == "WAIT_APPROVAL"
    request = next(row for row in model.requests if len(row.messages) > 2)
    observation = json.loads(request.messages[-1].content)["tool_observations"][0]
    assert observation["tool_name"] == name
    assert isinstance(observation["result"], list if name in {
        "get_impacted_reservations", "list_equipment_candidates",
    } else dict)
    if name == "validate_candidate_plan":
        offered = json.loads(request.messages[1].content)["offered_plans"][0]
        assert observation["bound_plan_id"] == offered["plan_id"]
        assert observation["result"]["passed"] is True
    if name == "get_booking_rules":
        assert observation["result"] == port.get_booking_rules().model_dump(mode="json")
    if name == "append_run_step":
        marker = next(step for step in port.steps if step.id == observation["result"]["step_id"])
        assert marker.tool_name == "append_run_step" and marker.model_calls == 0
    messages = " ".join(message.content for row in model.requests for message in row.messages)
    assert all(value not in messages for value in (
        "TEST_INJECTED_PRIVATE_REASON", "TEST_INJECTED_PRIVATE_ALIAS", "TEST_INJECTED_PRIVATE_NAME",
        "borrower_alias",
    ))


@pytest.mark.parametrize("duplicate_in_batch", [True, False])
def test_duplicate_requests_stop_before_reexecuting_tools(duplicate_in_batch):
    port = MemoryReadonly()
    rule_reads = []
    original = port.get_booking_rules

    def read_rules():
        rule_reads.append(True)
        return original()

    port.get_booking_rules = read_rules
    request = {"name": "get_booking_rules", "reservation_id": None}
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "tools": [request, request] if duplicate_in_batch else [request],
    }})
    result = execute(port, model)
    assert result.status == "FAILED"
    assert result.error.message == ("REPEATED_TOOL_REQUEST" if duplicate_in_batch else "TOOL_ROUND_LIMIT_REACHED")
    assert len(rule_reads) == (2 if duplicate_in_batch else 3)  # scope + mandatory Impact + at most one requested read
    assert len(model.requests) == (2 if duplicate_in_batch else 3)
    assert not port.validation_calls and not result.changes


@pytest.mark.parametrize("bad_request", [
    {"name": "execute_sql", "reservation_id": None},
    {"name": "list_equipment_candidates", "reservation_id": "outside_scope"},
])
def test_entire_tool_batch_is_authorized_before_even_its_first_read(bad_request):
    port = MemoryReadonly()
    original = port.get_booking_rules
    reads = []

    def read_rules():
        reads.append(True)
        return original()

    port.get_booking_rules = read_rules
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}, bad_request],
    }})
    result = execute(port, model)
    assert result.status == "FAILED" and len(model.requests) == 2
    assert len(reads) == 2 and not port.validation_calls
    assert not any(step.node == "core.tool_observation" for step in port.steps)


@pytest.mark.parametrize("bad_request,error", [
    ({"name": "execute_sql", "reservation_id": None}, "TOOL_ROUND_LIMIT_REACHED"),
    ({"name": "list_equipment_candidates", "reservation_id": "outside_scope"}, "TOOL_ROUND_LIMIT_REACHED"),
    ({"name": "get_incident", "reservation_id": "outside_scope"}, "TOOL_ROUND_LIMIT_REACHED"),
    ({"name": "get_incident", "reservation_id": None, "sql": "TEST_INJECTED"}, "MODEL_STRUCTURED_OUTPUT_INVALID"),
])
def test_followup_cannot_escape_the_original_role_permissions_or_argument_schema(bad_request, error):
    port = MemoryReadonly()
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}] if index == 0 else [bad_request],
    }})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == error
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 3
    assert not port.validation_calls and not result.changes


def test_bounded_tool_rounds_cannot_turn_a_provisional_answer_into_success():
    port = MemoryReadonly()
    names = ("get_booking_rules", "get_incident", "get_impacted_reservations")
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "tools": [{"name": names[index], "reservation_id": None}],
    }})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TOOL_ROUND_LIMIT_REACHED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 3
    assert len([step for step in port.steps if step.node == "core.tool_observation"]) == 1
    assert not port.validation_calls and not result.changes


def test_readonly_tool_failure_stops_without_a_followup_or_stale_plan():
    port = MemoryReadonly()
    original = port.get_booking_rules
    reads = []

    def failing_rules():
        reads.append(True)
        if len(reads) == 3:
            raise RuntimeError("TEST_INJECTED_PRIVATE_TOOL_ERROR")
        return original()

    port.get_booking_rules = failing_rules
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}],
    }})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "READONLY_TOOL_FAILED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 2
    assert not port.validation_calls and not result.changes
    assert "TEST_INJECTED_PRIVATE_TOOL_ERROR" not in result.model_dump_json() + str(port.steps)


def test_updated_daily_usage_blocks_the_followup_without_reusing_the_initial_plan():
    port = MemoryReadonly()

    def exhaust(result, payload, index):
        port.daily_calls = 30
        return {**result, "tools": [{"name": "get_booking_rules", "reservation_id": None}]}

    model = InjectedModel({"PlanningDecision": exhaust})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "DAILY_MODEL_BUDGET_EXCEEDED"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 2
    assert not port.validation_calls and not result.changes


def test_validation_tool_binds_only_an_offered_provisional_plan():
    port = MemoryReadonly()
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "plan_id": "TEST_INJECTED_unoffered", "tools": [{
            "name": "validate_candidate_plan", "reservation_id": None,
        }],
    }})
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "TOOL_PLAN_OUT_OF_SCOPE"
    assert len(model.requests) == 2 and not port.validation_calls


def test_role_conversations_and_repeated_runs_have_independent_observations():
    def read_once(result, payload, index):
        return result if payload.get("tool_observations") else {
            **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}],
        }

    core = GearShiftOrchestrator()
    model = InjectedModel({title: read_once for title in ("ImpactReport", "PlanningDecision", "VerificationReport")})
    observation_ids = []
    for run_id in ("run_first_observed", "run_second_observed"):
        port = MemoryReadonly(run_id=run_id)
        assert core.run_incident(port.incident.id, port.run_id, port, model).status == "WAIT_APPROVAL"
        requests = [row for row in model.requests if row.run_id == run_id]
        assert len(requests) == sum(step.model_calls for step in port.steps) == 6
        for request in requests:
            if len(request.messages) == 2:
                continue
            assert len(request.messages) == 4
            scope = json.loads(request.messages[-1].content)["observation_scope"]
            role = next(config.role for config in port.configs if config.agent_id == request.agent_id)
            parent = next(step for step in port.steps if step.node == "core.model_start" and step.agent_id == request.agent_id)
            assert scope == {"run_id": run_id, "incident_id": port.incident.id, "agent_id": request.agent_id,
                             "role": role, "request_digest": parent.input_digest}
            observations = json.loads(request.messages[-1].content)["tool_observations"]
            assert len(observations) == 1
            observation = observations[0]
            assert observation["request_digest"] == parent.input_digest
            assert observation["run_id"] == run_id and observation["role"] == role
            assert observation["source"]["port"] == "ReadonlyPort"
            observation_ids.append(observation["observation_id"])
    assert len(model.requests) == 12
    assert len(set(observation_ids)) == 6


@pytest.mark.parametrize("large", [False, True])
def test_trusted_fictional_rule_body_is_preserved_but_oversized_observations_fail_closed(large):
    class InjectedRuleMaterial(BookingRules):
        # Private forward-compatibility fixture, not a second public contract.
        test_fictional_rule_body: str

    port = MemoryReadonly()
    original = port.get_booking_rules
    reads = []
    text = "TEST_INJECTED fictional rules: preserve original booking windows."
    text *= 20000 if large else 1

    def read_rules():
        reads.append(True)
        return original() if len(reads) < 3 else InjectedRuleMaterial(test_fictional_rule_body=text)

    port.get_booking_rules = read_rules
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: result
        if payload.get("tool_observations") else {
            **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}],
        }})
    result = execute(port, model)
    if large:
        assert result.status == "FAILED" and result.error.message == "TOOL_OBSERVATION_TOO_LARGE"
        assert len(model.requests) == 2 and not port.validation_calls
    else:
        assert result.status == "WAIT_APPROVAL" and len(model.requests) == 4
        observation = json.loads(model.requests[2].messages[-1].content)["tool_observations"][0]
        assert observation["result"]["test_fictional_rule_body"] == text
