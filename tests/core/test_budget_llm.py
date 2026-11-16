"""TEST_INJECTED SDK responses and local HTTP transports; no network/provider account."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import subprocess
import sys

import httpx
from openai import OpenAI as RealSDK
import pytest

from campusops.agent_core import CoreSettings, GearShiftOrchestrator, LLMAdapter, ModelSettings
from campusops.agent_core.budget import BudgetLedger
from campusops.agent_core.errors import CoreFailure
from campusops.agent_core import budget as budget_module, llm as llm_module
from campusops.agent_core.roles import ImpactReport
from campusops.contracts.models import ErrorCode, ModelMessage, ModelRequest
from .support import InjectedModel, MemoryReadonly


TEST_KEY = "TEST_INJECTED_SECRET_NEVER_PERSIST"


def settings(**patch):
    return ModelSettings(api_key=TEST_KEY, base_url="https://provider.example.invalid/v1",
                         model_id="fictional-test-model", enabled=True, **patch)


def request(run_id="run_test"):
    return ModelRequest(run_id=run_id, agent_id="impact-agent",
                        messages=(ModelMessage(role="user", content="Fictional TEST_INJECTED input"),),
                        response_schema=json.dumps(ImpactReport.model_json_schema()))


def test_missing_env_configuration_never_constructs_provider(monkeypatch):
    for name in ("MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(CoreFailure, match="UNCONFIGURED"):
        LLMAdapter.from_env()


@pytest.mark.parametrize("name,value", [
    ("MODEL_CALLS_PER_RUN_MAX", "0"), ("MODEL_DAILY_CALLS_MAX", "invalid"),
    ("REPLAN_MAX", "3"), ("REPLAN_MAX", "-1"),
])
def test_invalid_limits_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(CoreFailure, match="INVALID_CONFIGURATION"):
        CoreSettings.from_env()


@pytest.mark.parametrize("base_url", [
    "http://provider.example.invalid/v1", "https://user:secret@example.invalid/v1",
    "https://example.invalid/v1?api_key=secret", "https://example.invalid/v1#secret",
])
def test_url_cannot_hide_credentials_or_silently_select_an_insecure_endpoint(base_url):
    with pytest.raises(CoreFailure):
        ModelSettings(api_key=TEST_KEY, base_url=base_url, model_id="fictional", enabled=True)


def test_env_controls_timeout_replan_and_explicit_enable(monkeypatch):
    for name, value in {"MODEL_API_KEY": TEST_KEY, "MODEL_BASE_URL": "https://provider.example.invalid/v1",
                        "MODEL_ID": "fictional-test-model", "CAMPUSOPS_LLM_ENABLED": "true",
                        "MODEL_TIMEOUT_SECONDS": "7.5", "REPLAN_MAX": "1"}.items():
        monkeypatch.setenv(name, value)
    config = ModelSettings.from_env()
    assert config.enabled and config.timeout_seconds == 7.5 and config.limits.max_replans == 1
    assert TEST_KEY not in repr(config)


def test_disabled_adapter_has_no_budget_or_provider_side_effects(tmp_path):
    ledger = BudgetLedger(tmp_path / "quota")
    adapter = LLMAdapter(replace(settings(), enabled=False), budget=ledger)
    with pytest.raises(CoreFailure, match="UNCONFIGURED") as caught:
        adapter.complete(request())
    assert not caught.value.attempted and not ledger.directory.exists()


def test_debug_logging_is_refused_before_any_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_LOG", "debug")
    ledger = BudgetLedger(tmp_path / "quota")
    with pytest.raises(CoreFailure, match="DEBUG_LOGGING"):
        LLMAdapter(settings(), budget=ledger).complete(request())
    assert not ledger.directory.exists()


def test_ledger_persists_budgets_across_instances_and_demo_log_reset(tmp_path):
    limits = CoreSettings(calls_per_run=2, daily_calls=3)
    BudgetLedger(tmp_path).reserve("run_one", limits)
    BudgetLedger(tmp_path).reserve("run_one", limits)
    with pytest.raises(CoreFailure, match="RUN_MODEL_BUDGET_EXCEEDED"):
        BudgetLedger(tmp_path).reserve("run_one", limits)
    BudgetLedger(tmp_path).reserve("run_two", limits)
    with pytest.raises(CoreFailure, match="DAILY_MODEL_BUDGET_EXCEEDED"):
        BudgetLedger(tmp_path).reserve("run_three", limits)
    assert len((tmp_path / "usage.jsonl").read_text().splitlines()) == 3


def test_concurrent_instances_reserve_no_more_than_daily_limit(tmp_path):
    limits = CoreSettings(calls_per_run=6, daily_calls=5)
    def attempt(index):
        try:
            BudgetLedger(tmp_path).reserve(f"run_{index}", limits)
            return True
        except CoreFailure:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(20)))
    assert sum(results) == 5
    assert [json.loads(row)["daily_count"] for row in (tmp_path / "usage.jsonl").read_text().splitlines()] == [1, 2, 3, 4, 5]


def test_independent_process_obeys_persisted_limit(tmp_path):
    BudgetLedger(tmp_path).reserve("run_one", CoreSettings(daily_calls=1))
    code = "\n".join([
        "from pathlib import Path", "import sys",
        "from campusops.agent_core.budget import BudgetLedger",
        "from campusops.agent_core.config import CoreSettings",
        "from campusops.agent_core.errors import CoreFailure",
        "try:", "    BudgetLedger(Path(sys.argv[1])).reserve('run_two', CoreSettings(daily_calls=1))",
        "except CoreFailure as exc:", "    print(exc.error.message)", "    sys.exit(0)", "sys.exit(1)",
    ])
    completed = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0 and "DAILY_MODEL_BUDGET_EXCEEDED" in completed.stdout


def test_os_lock_blocks_an_independent_process_without_reserving(tmp_path):
    code = "\n".join([
        "from pathlib import Path", "import sys",
        "from campusops.agent_core.budget import BudgetLedger",
        "from campusops.agent_core.config import CoreSettings",
        "from campusops.agent_core.errors import CoreFailure",
        "try:", "    BudgetLedger(Path(sys.argv[1])).reserve('run_two', CoreSettings())",
        "except CoreFailure:", "    sys.exit(0)", "sys.exit(1)",
    ])
    with BudgetLedger(tmp_path)._exclusive():
        completed = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, timeout=30)
    assert completed.returncode == 0 and not (tmp_path / "usage.jsonl").exists()


def test_budget_path_cannot_escape_this_worktree():
    outside = Path(__file__).resolve().parents[3].parent / "outside-core-test"
    with pytest.raises(CoreFailure, match="OUTSIDE_WORKTREE"):
        BudgetLedger(outside)


def test_utc_rollover_resets_daily_but_preserves_per_run_limit(tmp_path, monkeypatch):
    limits = CoreSettings(calls_per_run=1, daily_calls=1)
    BudgetLedger(tmp_path).reserve("run_one", limits)
    class NextDay(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(days=1)
    monkeypatch.setattr(budget_module, "datetime", NextDay)
    with pytest.raises(CoreFailure, match="RUN_MODEL_BUDGET_EXCEEDED"):
        BudgetLedger(tmp_path).reserve("run_one", limits)
    BudgetLedger(tmp_path).reserve("run_two", limits)


@pytest.mark.parametrize("data", [b"partial", b'{"version":1}\n', b"\n"])
def test_corrupt_or_incomplete_ledger_is_preserved_and_fails_closed(tmp_path, data):
    path = tmp_path / "usage.jsonl"
    path.write_bytes(data)
    with pytest.raises(CoreFailure, match="BUDGET_LEDGER_UNAVAILABLE_OR_INVALID"):
        BudgetLedger(tmp_path).reserve("run_one", CoreSettings())
    assert path.read_bytes() == data


def install_local_transport(monkeypatch, handler):
    options = []
    def factory(**kwargs):
        options.append(dict(kwargs))
        provided = kwargs.pop("http_client", None)
        hooks = provided.event_hooks if provided is not None else None
        if provided:
            provided.close()
        return RealSDK(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False,
                                                         event_hooks=hooks))
    monkeypatch.setattr(llm_module, "OpenAI", factory)
    return options


def completion_body(content="{}", finish_reason="stop", usage=True):
    return {"id": "TEST_INJECTED_completion", "object": "chat.completion", "created": 1,
            "model": "fictional-test-model", "choices": [{"index": 0, "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content}}],
            **({"usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}} if usage else {})}


def test_real_sdk_uses_explicit_endpoint_schema_timeout_and_zero_retries(tmp_path, monkeypatch):
    captured = []
    def handler(incoming):
        captured.append(incoming)
        return httpx.Response(200, json=completion_body())
    options = install_local_transport(monkeypatch, handler)
    result = LLMAdapter(settings(timeout_seconds=4.5), budget=BudgetLedger(tmp_path)).complete(request())
    assert result.provider == "openai_compatible" and (result.input_tokens, result.output_tokens) == (7, 5)
    assert len(captured) == 1 and str(captured[0].url) == "https://provider.example.invalid/v1/chat/completions"
    payload = json.loads(captured[0].content)
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["model"] == "fictional-test-model"
    assert options[0]["timeout"] == 4.5 and options[0]["max_retries"] == 0
    assert not options[0]["http_client"].follow_redirects
    assert TEST_KEY not in (tmp_path / "usage.jsonl").read_text()


@pytest.mark.parametrize("status,code", [(429, ErrorCode.BUDGET_EXCEEDED), (500, ErrorCode.PROVIDER_UNAVAILABLE),
                                        (401, ErrorCode.PROVIDER_UNAVAILABLE)])
def test_http_errors_are_redacted_and_not_retried(tmp_path, monkeypatch, status, code):
    attempted = []
    def handler(incoming):
        attempted.append(incoming)
        return httpx.Response(status, json={"error": {"message": TEST_KEY, "type": "injected"}})
    install_local_transport(monkeypatch, handler)
    with pytest.raises(CoreFailure) as caught:
        LLMAdapter(settings(), budget=BudgetLedger(tmp_path)).complete(request())
    assert caught.value.error.code == code and caught.value.attempted
    assert TEST_KEY not in str(caught.value) and len(attempted) == 1
    assert caught.value.error.message == ("PROVIDER_RATE_OR_QUOTA_LIMIT" if status == 429 else f"PROVIDER_HTTP_{status}")
    assert len((tmp_path / "usage.jsonl").read_text().splitlines()) == 1


def test_timeout_consumes_reservation_and_is_not_retried(tmp_path, monkeypatch):
    calls = []
    def handler(incoming):
        calls.append(incoming)
        raise httpx.ReadTimeout(TEST_KEY, request=incoming)
    install_local_transport(monkeypatch, handler)
    limits = CoreSettings(calls_per_run=1)
    adapter = LLMAdapter(settings(limits=limits), budget=BudgetLedger(tmp_path))
    with pytest.raises(CoreFailure, match="MODEL_TIMEOUT") as caught:
        adapter.complete(request())
    assert caught.value.attempted and len(calls) == 1
    with pytest.raises(CoreFailure, match="RUN_MODEL_BUDGET_EXCEEDED") as caught:
        adapter.complete(request())
    assert not caught.value.attempted and len(calls) == 1


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls"])
def test_incomplete_or_refused_output_is_not_success(tmp_path, monkeypatch, finish_reason):
    install_local_transport(monkeypatch, lambda incoming: httpx.Response(200, json=completion_body(finish_reason=finish_reason)))
    with pytest.raises(CoreFailure, match="MODEL_OUTPUT_INCOMPLETE"):
        LLMAdapter(settings(), budget=BudgetLedger(tmp_path)).complete(request())


def test_missing_provider_token_counts_remain_unknown(tmp_path, monkeypatch):
    install_local_transport(monkeypatch, lambda incoming: httpx.Response(200, json=completion_body(usage=False)))
    response = LLMAdapter(settings(), budget=BudgetLedger(tmp_path)).complete(request())
    assert response.input_tokens is None and response.output_tokens is None


def test_redirect_does_not_switch_endpoints(tmp_path, monkeypatch):
    calls = []
    def handler(incoming):
        calls.append(incoming)
        return httpx.Response(307, headers={"location": "https://other.example.invalid/v1/chat/completions"})
    install_local_transport(monkeypatch, handler)
    with pytest.raises(CoreFailure, match="PROVIDER_HTTP_307"):
        LLMAdapter(settings(), budget=BudgetLedger(tmp_path)).complete(request())
    assert len(calls) == 1


@pytest.mark.parametrize("value", ["false", 1])
def test_enable_flag_requires_a_boolean_in_explicit_configuration(value):
    with pytest.raises(CoreFailure, match="explicit boolean"):
        replace(settings(), enabled=value)


def test_sdk_failure_before_http_dispatch_is_not_counted_as_attempt(tmp_path, monkeypatch):
    dispatched = []
    install_local_transport(monkeypatch, lambda incoming: dispatched.append(incoming))
    adapter = LLMAdapter(replace(settings(), api_key="TEST_INJECTED_\u2603"), budget=BudgetLedger(tmp_path))
    with pytest.raises(CoreFailure) as caught:
        adapter.complete(request())
    assert not dispatched
    assert not caught.value.attempted
    assert caught.value.error.message == "MODEL_REQUEST_BUILD_FAILED"
    # An uncertain/prepared reservation is retained; it is not an HTTP attempt.
    assert len((tmp_path / "usage.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_json_schema_is_rejected_before_budget_or_http(tmp_path, monkeypatch, constant):
    dispatched = []
    install_local_transport(monkeypatch, lambda incoming: dispatched.append(incoming))
    ledger = BudgetLedger(tmp_path / "quota")
    malformed = request().model_copy(update={"response_schema":
        '{"type":"object","properties":{"score":{"type":"number","minimum":' + constant + '}}}'})
    with pytest.raises(CoreFailure) as caught:
        LLMAdapter(settings(), budget=ledger).complete(malformed)
    assert caught.value.error.code == ErrorCode.INVALID_INPUT
    assert not caught.value.attempted and not dispatched and not ledger.directory.exists()


def test_child_sdk_debug_logger_is_refused_before_private_input(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="openai._base_client")
    dispatched = []
    def handler(incoming):
        dispatched.append(incoming)
        return httpx.Response(200, json=completion_body())
    install_local_transport(monkeypatch, handler)
    ledger = BudgetLedger(tmp_path / "quota")
    private_marker = "TEST_FICTIONAL_PRIVATE_PROMPT"
    sensitive = request().model_copy(update={"messages": (ModelMessage(role="user", content=private_marker),)})
    with pytest.raises(CoreFailure, match="UNSAFE_PROVIDER_DEBUG_LOGGING"):
        LLMAdapter(settings(), budget=ledger).complete(sensitive)
    assert not dispatched and not ledger.directory.exists()
    assert private_marker not in caplog.text and TEST_KEY not in caplog.text


def test_actual_sdk_three_role_roundtrip_uses_strict_schemas_and_counts(tmp_path, monkeypatch):
    port = MemoryReadonly()
    injected = InjectedModel()
    titles = []

    def check_objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                check_objects(child)
        elif isinstance(node, list):
            for child in node:
                check_objects(child)

    def handler(incoming):
        body = json.loads(incoming.content)
        schema = body["response_format"]["json_schema"]["schema"]
        assert body["response_format"]["json_schema"]["strict"] is True
        check_objects(schema)
        titles.append(schema["title"])
        agent_id = {"ImpactReport": "impact-agent", "PlanningDecision": "planner-agent",
                    "VerificationReport": "verifier-agent"}[schema["title"]]
        result = injected.complete(ModelRequest(run_id=port.run_id, agent_id=agent_id,
                                   messages=body["messages"], response_schema=json.dumps(schema)))
        return httpx.Response(200, json=completion_body(content=result.content))

    install_local_transport(monkeypatch, handler)
    adapter = LLMAdapter(settings(), budget=BudgetLedger(tmp_path))
    result = GearShiftOrchestrator().run_incident(port.incident.id, port.run_id, port, adapter)
    assert result.status == "WAIT_APPROVAL" and result.proposal_id is None
    assert titles == ["ImpactReport", "PlanningDecision", "VerificationReport"]
    assert sum(row.model_calls for row in port.steps) == 3
    assert len((tmp_path / "usage.jsonl").read_text().splitlines()) == 3
    assert len(port.validation_calls) == 2


@pytest.mark.parametrize("budget_limit,failure,expected,calls", [
    (6, None, "WAIT_APPROVAL", 4),
    (2, None, "FAILED", 2),
    (6, "timeout", "FAILED", 3),
    (6, "quota", "FAILED", 3),
    (6, "invalid-json", "FAILED", 3),
    (6, "usage-unknown", "WAIT_APPROVAL", 4),
])
def test_sdk_tool_followup_reuses_persistent_budget_and_never_retries_or_redirects(
        tmp_path, monkeypatch, budget_limit, failure, expected, calls):
    """Real SDK plus TEST_INJECTED MockTransport, not a cloud execution."""
    port = MemoryReadonly()

    def choose_after_observation(result, payload, index):
        if not payload.get("tool_observations"):
            return {**result, "tools": [{"name": "get_booking_rules", "reservation_id": None}]}
        assert payload["tool_observations"][0]["result"]["preserve_original_times"] is True
        return {**result, "plan_id": payload["offered_plans"][-1]["plan_id"]}

    injected = InjectedModel({"PlanningDecision": choose_after_observation})
    captured = []

    def handler(incoming):
        body = json.loads(incoming.content)
        captured.append(body)
        assert str(incoming.url) == "https://provider.example.invalid/v1/chat/completions"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["max_completion_tokens"] == 1600
        schema = body["response_format"]["json_schema"]["schema"]
        followup = schema["title"] == "PlanningDecision" and len(body["messages"]) > 2
        if followup:
            assert json.loads(body["messages"][-1]["content"])["tool_observations"]
            if failure == "timeout":
                raise httpx.ReadTimeout(TEST_KEY, request=incoming)
            if failure == "quota":
                return httpx.Response(429, json={"error": {"message": TEST_KEY}})
            if failure == "invalid-json":
                return httpx.Response(200, json=completion_body("{TEST_INJECTED_INVALID_JSON"))
        agent_id = {"ImpactReport": "impact-agent", "PlanningDecision": "planner-agent",
                    "VerificationReport": "verifier-agent"}[schema["title"]]
        response = injected.complete(ModelRequest(run_id=port.run_id, agent_id=agent_id,
                                                  messages=body["messages"], response_schema=json.dumps(schema)))
        return httpx.Response(200, json=completion_body(response.content, usage=failure != "usage-unknown"))

    options = install_local_transport(monkeypatch, handler)
    adapter = LLMAdapter(settings(limits=CoreSettings(calls_per_run=budget_limit)), budget=BudgetLedger(tmp_path))
    original = port.snapshot.model_dump_json()
    result = GearShiftOrchestrator().run_incident(port.incident.id, port.run_id, port, adapter)
    assert result.status == expected
    assert len(captured) == sum(step.model_calls for step in port.steps) == calls
    assert len((tmp_path / "usage.jsonl").read_text().splitlines()) == calls
    assert all(option["max_retries"] == 0 and not option["http_client"].follow_redirects for option in options)
    assert port.snapshot.model_dump_json() == original
    assert TEST_KEY not in result.model_dump_json() + str(port.steps)
    completions = [step for step in port.steps if step.node == "core.model" and step.model_calls]
    if failure == "usage-unknown":
        assert all("input_tokens=None;output_tokens=None" in step.output_digest for step in completions)
    elif failure == "invalid-json":
        assert "input_tokens=7;output_tokens=5" in completions[-1].output_digest
    if expected == "WAIT_APPROVAL":
        assert result.changes[0].to_equipment_id == "camera_b"
        assert len(port.validation_calls) == 2
    else:
        assert not result.changes and not port.validation_calls


def test_pre_dispatch_sdk_failure_keeps_reservation_but_logs_zero_calls(tmp_path, monkeypatch):
    port = MemoryReadonly()
    dispatched = []
    install_local_transport(monkeypatch, lambda incoming: dispatched.append(incoming))
    adapter = LLMAdapter(replace(settings(), api_key="TEST_INJECTED_\u2603"), budget=BudgetLedger(tmp_path))
    result = GearShiftOrchestrator().run_incident(port.incident.id, port.run_id, port, adapter)
    assert result.status == "FAILED" and result.error.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert sum(row.model_calls for row in port.steps) == 0 and not dispatched
    assert len((tmp_path / "usage.jsonl").read_text().splitlines()) == 1


@pytest.mark.parametrize("mode", ["default", "disabled", "enabled"])
def test_thinking_is_explicit_passthrough_without_changing_token_cap(tmp_path, monkeypatch, mode):
    bodies = []
    def handler(incoming):
        bodies.append(json.loads(incoming.content))
        return httpx.Response(200, json=completion_body())
    install_local_transport(monkeypatch, handler)
    LLMAdapter(settings(thinking=mode), budget=BudgetLedger(tmp_path)).complete(request())
    assert len(bodies) == 1 and bodies[0]["max_completion_tokens"] == 1600
    assert "tools" not in bodies[0] and not bodies[0].get("stream", False)
    if mode == "default":
        assert "thinking" not in bodies[0]
    else:
        assert bodies[0]["thinking"] == {"type": mode}


@pytest.mark.parametrize("mode", ["default", "disabled", "enabled"])
def test_thinking_environment_is_a_manual_setting(monkeypatch, mode):
    for name, value in {"MODEL_API_KEY": TEST_KEY, "MODEL_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
                        "MODEL_ID": "doubao-seed-2-1-lite-260915", "MODEL_THINKING": mode}.items():
        monkeypatch.setenv(name, value)
    assert ModelSettings.from_env().thinking == mode


@pytest.mark.parametrize("mode", ["auto", "DISABLED", "true", "", "disabled "])
def test_unknown_thinking_configuration_is_rejected_without_echoing_value(mode):
    with pytest.raises(CoreFailure, match="INVALID_CONFIGURATION: MODEL_THINKING"):
        settings(thinking=mode)


def test_reasoning_fields_are_not_returned_or_audited(tmp_path, monkeypatch):
    body = completion_body()
    body["choices"][0]["message"].update({
        "reasoning_content": "TEST_PRIVATE_REASONING", "encrypted_content": "TEST_PRIVATE_ENCRYPTED_THOUGHTS",
    })
    install_local_transport(monkeypatch, lambda incoming: httpx.Response(200, json=body))
    response = LLMAdapter(settings(thinking="disabled"), budget=BudgetLedger(tmp_path)).complete(request())
    assert "TEST_PRIVATE" not in response.model_dump_json()
    assert "TEST_PRIVATE" not in (tmp_path / "usage.jsonl").read_text()
