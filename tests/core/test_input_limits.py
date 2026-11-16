"""TEST_INJECTED oversized requests; no provider, tokens or paid API."""

import pytest

from campusops.agent_core import GearShiftOrchestrator, LLMAdapter
from campusops.agent_core.budget import BudgetLedger
from campusops.agent_core.errors import CoreFailure
from campusops.agent_core.input_limits import MAX_MODEL_INPUT_BYTES
from campusops.contracts.models import BookingRules, ModelMessage
from .support import InjectedModel, MemoryReadonly
from .test_budget_llm import install_local_transport, request, settings


class InjectedOversizedRules(BookingRules):
    test_fictional_rule_body: str


def test_initial_oversized_core_input_stops_before_any_model_invocation():
    port = MemoryReadonly()
    port.get_booking_rules = lambda: InjectedOversizedRules(
        test_fictional_rule_body="X" * MAX_MODEL_INPUT_BYTES,
    )
    model = InjectedModel()
    result = GearShiftOrchestrator().run_incident(port.incident.id, port.run_id, port, model)
    assert result.status == "FAILED" and result.error.message == "MODEL_INPUT_TOO_LARGE"
    assert not model.requests and not port.validation_calls
    assert sum(step.model_calls for step in port.steps) == 0
    assert not any(step.node == "core.model_start" for step in port.steps)


def test_followup_cumulative_input_is_bounded_even_when_the_observation_itself_fits():
    port = MemoryReadonly()
    original = port.get_booking_rules
    reads = []

    def read_rules():
        reads.append(True)
        if len(reads) < 3:
            return original()
        return InjectedOversizedRules(test_fictional_rule_body="X" * (MAX_MODEL_INPUT_BYTES - 2048))

    port.get_booking_rules = read_rules
    model = InjectedModel({"PlanningDecision": lambda result, payload, index: {
        **result, "tools": [{"name": "get_booking_rules", "reservation_id": None}],
    }})
    result = GearShiftOrchestrator().run_incident(port.incident.id, port.run_id, port, model)
    assert result.status == "FAILED" and result.error.message == "MODEL_INPUT_TOO_LARGE"
    assert len(model.requests) == sum(step.model_calls for step in port.steps) == 2
    assert any(step.node == "core.tool_observation" for step in port.steps)
    assert not result.changes and not port.validation_calls


def test_direct_sdk_input_limit_precedes_reservation_client_creation_and_http(tmp_path, monkeypatch):
    options = install_local_transport(monkeypatch, lambda incoming: pytest.fail("No HTTP dispatch is allowed"))
    oversized = request().model_copy(update={
        "messages": (ModelMessage(role="user", content="X" * MAX_MODEL_INPUT_BYTES),),
    })
    adapter = LLMAdapter(settings(), budget=BudgetLedger(tmp_path))
    with pytest.raises(CoreFailure, match="MODEL_INPUT_TOO_LARGE") as caught:
        adapter.complete(oversized)
    assert not caught.value.attempted and not options
    assert not (tmp_path / "usage.jsonl").exists()
