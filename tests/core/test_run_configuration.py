"""TEST_INJECTED per-run registry and actual prompt-version evidence."""

import hashlib
import json

import pytest

from campusops.agent_core import GearShiftOrchestrator
from .support import InjectedModel, MemoryReadonly


def execute(port, model):
    return GearShiftOrchestrator().run_incident(port.incident.id, port.run_id, port, model)


def test_prompt_versions_select_different_bodies_and_audit_exact_body_hash():
    bodies = {}
    for version in ("v1", "v2"):
        port = MemoryReadonly(run_id="run_" + version)
        port.configs = tuple(row.model_copy(update={"prompt_version": version}) for row in port.configs)
        model = InjectedModel()
        assert execute(port, model).status == "WAIT_APPROVAL"
        for request in model.requests:
            body = request.messages[0].content
            bodies[version, request.agent_id] = body.replace(version, "<VERSION>")
            started = next(step for step in port.steps
                           if step.node == "core.model_start" and step.agent_id == request.agent_id)
            assert "prompt_version=" + version in started.output_digest
            assert "body_sha256=" + hashlib.sha256(body.encode("utf-8")).hexdigest() in started.output_digest
    for agent in ("impact-agent", "planner-agent", "verifier-agent"):
        assert bodies["v1", agent] != bodies["v2", agent]


@pytest.mark.parametrize("role", ["impact", "planner", "verifier"])
def test_unknown_prompt_version_stops_before_any_model_request(role):
    port = MemoryReadonly()
    port.configs = tuple(row.model_copy(update={"prompt_version": "unknown-test-version"})
                         if row.role == role else row for row in port.configs)
    model = InjectedModel()
    result = execute(port, model)
    assert result.status == "FAILED" and result.error.message == "UNKNOWN_PROMPT_VERSION"
    assert not model.requests and not port.validation_calls


def test_registry_is_read_once_and_mid_run_changes_affect_only_next_run():
    port = MemoryReadonly()
    registry_reads = []

    def registry():
        registry_reads.append(True)
        return port.configs

    port.list_agent_configs = registry

    def disable_planner(result, payload, index):
        port.configs = tuple(row.model_copy(update={"enabled": False, "prompt_version": "v2"})
                             if row.role == "planner" else row for row in port.configs)
        return result

    model = InjectedModel({"ImpactReport": disable_planner})
    first = execute(port, model)
    assert first.status == "WAIT_APPROVAL" and len(model.requests) == 3
    assert len(registry_reads) == 1
    assert "Prompt version: v1" in model.requests[1].messages[0].content
    next_port = MemoryReadonly(run_id="run_next")
    next_port.configs = port.configs
    next_model = InjectedModel()
    assert execute(next_port, next_model).status == "NEEDS_MANUAL"
    assert not next_model.requests


def test_prompt_body_and_optional_tool_permissions_are_frozen_until_the_next_run():
    port = MemoryReadonly()

    def change_planner(result, payload, index):
        port.configs = tuple(row.model_copy(update={
            "prompt_version": "v2",
            "allowed_tools": tuple(name for name in row.allowed_tools if name != "append_run_step"),
        }) if row.role == "planner" else row for row in port.configs)
        return result

    first_model = InjectedModel({"ImpactReport": change_planner})
    assert execute(port, first_model).status == "WAIT_APPROVAL"
    first_planner = first_model.requests[1]
    assert "Prompt version: v1" in first_planner.messages[0].content
    assert "append_run_step" in json.loads(first_planner.messages[1].content)["allowed_tools"]
    next_port = MemoryReadonly(run_id="run_new_prompt_and_permissions")
    next_port.configs = port.configs
    next_model = InjectedModel()
    assert execute(next_port, next_model).status == "WAIT_APPROVAL"
    next_planner = next_model.requests[1]
    assert "Prompt version: v2" in next_planner.messages[0].content
    assert "append_run_step" not in json.loads(next_planner.messages[1].content)["allowed_tools"]
    assert first_planner.messages[0].content != next_planner.messages[0].content
