"""Explicit opt-in only. Never invoked automatically or counted as offline evidence."""

import os

import pytest

from campusops.agent_core import GearShiftOrchestrator, LLMAdapter
from campusops.agent_core.errors import CoreFailure
from campusops.contracts.models import ExecutionMode, IncidentCreate


@pytest.mark.real_api
def test_real_three_role_workflow(main_factory):
    # --run-real-api AND enabled env AND a manually configured endpoint/key/model
    # are all required. This test never sets enable flags or obtains a paid key.
    if os.environ.get("CAMPUSOPS_LLM_ENABLED", "false").lower() != "true":
        pytest.skip("NotRun: explicitly enable real API only after checking free quota/billing protection")
    try:
        client = LLMAdapter.from_env()
    except CoreFailure:
        pytest.skip("NotRun: real provider configuration is unavailable")
    service = main_factory(GearShiftOrchestrator(), client, ExecutionMode.REAL_API)
    incident = service.register_equipment_failure(IncidentCreate(
        equipment_id="cam-01", effective_at="2026-11-10T00:00:00Z", reason="Fictional real-API acceptance incident",
    ))
    original = service.list_reservations()
    result = service.start_workflow(incident.id)
    assert result.status == "WAIT_APPROVAL", result.error.code if result.error else result.status
    assert service.list_reservations() == original
    steps = service.list_run_steps(result.run_id)
    models = [row for row in steps if row.node == "core.model" and row.model_calls == 1]
    assert {row.agent_id for row in models} == {"impact-agent", "planner-agent", "verifier-agent"}
    assert all(row.execution_mode == ExecutionMode.REAL_API for row in steps)
    assert 3 <= service.get_run(result.run_id).model_calls <= client.settings.limits.calls_per_run
