"""Per-run model/tool execution and factual, redacted audit events."""

from datetime import datetime, timezone
import hashlib
import json
import threading
from time import monotonic, perf_counter

from pydantic import BaseModel, ValidationError

from campusops.agent_core.config import CoreSettings
from campusops.agent_core.errors import CoreFailure
from campusops.agent_core.input_limits import MAX_MODEL_INPUT_BYTES, require_bounded_input
from campusops.agent_core.observations import tool_result_payload
from campusops.agent_core.prompts import resolve_prompt
from campusops.agent_core.registry import Route, TOOLS
from campusops.agent_core.roles import PlanningDecision, ToolRequest, role_response_schema, tool_contracts
from campusops.contracts.errors import CampusOpsError
from campusops.contracts.configuration import ROLE_TOOL_LIMITS
from campusops.contracts.models import (
    AgentConfig, ErrorCode, ModelMessage, ModelRequest, ModelResponse, ProposalItem,
    RunStatus, RunStepCreate, ServiceError,
)
from campusops.contracts.ports import ModelClientPort, ReadonlyPort


MAX_TOOL_ROUNDS = 1
# Accommodate the published 32 x 2000-character rule catalog even when
# canonical JSON escapes every character; never silently truncate an observation.
MAX_OBSERVATION_BYTES = 1048576


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      default=lambda obj: obj.model_dump(mode="json") if isinstance(obj, BaseModel) else str(obj))


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class RunContext:
    def __init__(self, run_id: str, incident_id: str, port: ReadonlyPort,
                 client: ModelClientPort | None, settings: CoreSettings, *, clock=None):
        self.run_id = run_id
        self.incident_id = incident_id
        self.port = port
        self.client = client
        self.settings = settings
        self._clock = clock if clock is not None else monotonic
        self._deadline = self._clock() + settings.task_timeout_seconds
        self._expired = False
        self.routes: dict[str, Route] = {}
        self.completed: set[str] = set()
        self.calls = 0
        self.retry = 0
        self.impacted_ids: set[str] = set()
        self.snapshot_fingerprint: str | None = None
        self.registry_snapshot: tuple[AgentConfig, ...] | None = None
        self._audit_lock = threading.Lock()
        # Fixed by trusted orchestration, never by model input or environment.
        self.prompt_resolver = resolve_prompt
        self.observation_encoder = tool_result_payload
        self.tool_contract_builder = tool_contracts
        self.operation = "equipment_failure"

    def check_deadline(self, *, attempted: bool = False):
        if self._expired or self._clock() >= self._deadline:
            self._expired = True
            raise CoreFailure(ErrorCode.WORKFLOW_FAILED, "TASK_DEADLINE_EXCEEDED", attempted=attempted)

    def log(self, node: str, reason: str, *, agent: AgentConfig | None = None,
            tool: str | None = None, inputs="", outputs="", duration_ms=0,
            error: ServiceError | None = None, model_calls=0, status: RunStatus | None = None):
        # All caller-supplied strings here are controlled templates, counts or hashes.
        with self._audit_lock:
            try:
                return self.port.append_run_step(RunStepCreate(
                    run_id=self.run_id, node=node, agent_id=agent.agent_id if agent else None,
                    tool_name=tool, decision_reason=reason[:500], input_digest=inputs[:500],
                    output_digest=outputs[:500], duration_ms=duration_ms, error=error,
                    model_calls=model_calls, retry_count=self.retry, status=status,
                ))
            except Exception:
                raise CoreFailure(ErrorCode.WORKFLOW_FAILED, "AUDIT_APPEND_FAILED") from None

    def system_read(self, name: str, *args):
        # Trusted orchestration reads, separate from model-selected role tools.
        self.check_deadline()
        if name not in {"get_snapshot", "list_agent_configs", "get_model_usage",
                        "get_incident", "get_impacted_reservations", "get_booking_rules"}:
            raise CoreFailure(ErrorCode.INVALID_INPUT, "UNKNOWN_ORCHESTRATION_READ")
        try:
            if name == "list_agent_configs":
                if self.registry_snapshot is None:
                    self.registry_snapshot = tuple(AgentConfig.model_validate(row)
                                                   for row in self.port.list_agent_configs())
                result = self.registry_snapshot
            else:
                result = getattr(self.port, name)(*args)
        except CampusOpsError as exc:
            raise CoreFailure(exc.error.code, "ORCHESTRATION_READ_FAILED") from None
        except Exception:
            raise CoreFailure(ErrorCode.WORKFLOW_FAILED, "ORCHESTRATION_READ_FAILED") from None
        self.check_deadline()
        return result

    def agent(self, role: str) -> AgentConfig:
        self.check_deadline()
        route = self.routes[role]
        if route.selected is None:
            raise CoreFailure(ErrorCode.CAPABILITY_MISMATCH, "REQUIRED_AGENT_UNAVAILABLE")
        if not set(route.task.dependencies) <= self.completed:
            raise CoreFailure(ErrorCode.INVALID_STATE, "AGENT_DEPENDENCIES_NOT_SATISFIED")
        return route.selected

    def _authorize_tool(self, role: str, name: str, reservation_id: str | None):
        agent = self.agent(role)
        if name not in TOOLS or name not in ROLE_TOOL_LIMITS[role] or name not in agent.allowed_tools:
            self.log("core.tool_rejected", "Tool request rejected by the role whitelist", agent=agent)
            raise CoreFailure(ErrorCode.INVALID_INPUT, "TOOL_PERMISSION_DENIED")
        if ((name == "list_equipment_candidates" and reservation_id not in self.impacted_ids)
                or (name != "list_equipment_candidates" and reservation_id is not None)):
            self.log("core.tool_rejected", "Tool arguments are outside the incident scope", agent=agent)
            raise CoreFailure(ErrorCode.INVALID_INPUT, "TOOL_ARGUMENTS_OUT_OF_SCOPE")
        return agent

    def tool(self, role: str, name: str, *, reservation_id: str | None = None,
             changes: tuple[ProposalItem, ...] = ()):
        agent = self._authorize_tool(role, name, reservation_id)
        self.check_deadline()
        started = perf_counter()
        try:
            if name == "get_incident":
                result = self.port.get_incident(self.incident_id)
            elif name == "get_impacted_reservations":
                result = self.port.get_impacted_reservations(self.incident_id)
            elif name == "get_booking_rules":
                result = self.port.get_booking_rules()
            elif name == "list_equipment_candidates":
                result = self.port.list_equipment_candidates(reservation_id)
            elif name == "validate_candidate_plan":
                result = self.port.validate_candidate_plan(changes)
            else:
                # A model may request a marker, never supply its contents/status/counts.
                result = None
        except CampusOpsError as exc:
            error = ServiceError(code=exc.error.code, message="READONLY_TOOL_FAILED")
            self.log("core.tool", "Readonly tool failed", agent=agent, tool=name, error=error)
            raise CoreFailure(error.code, error.message) from None
        except Exception:
            error = ServiceError(code=ErrorCode.WORKFLOW_FAILED, message="READONLY_TOOL_FAILED")
            self.log("core.tool", "Readonly tool failed", agent=agent, tool=name, error=error)
            raise CoreFailure(error.code, error.message) from None
        try:
            self.check_deadline()
        except CoreFailure as exc:
            self.log("core.tool", "Readonly result discarded after the task deadline", agent=agent, tool=name,
                     inputs=digest({"reservation_id": reservation_id, "changes": changes}),
                     outputs=digest(result), duration_ms=int((perf_counter() - started) * 1000), error=exc.error)
            raise
        audit_step = self.log("core.tool", "Agent requested an audit marker" if name == "append_run_step" else "Executed allowed readonly tool",
                 agent=agent, tool=name, inputs=digest({"reservation_id": reservation_id, "changes": changes}),
                 outputs=digest(result), duration_ms=int((perf_counter() - started) * 1000))
        self.check_deadline()
        return audit_step if name == "append_run_step" else result

    def requested_tools(self, role: str, requests: tuple[ToolRequest, ...], *, changes=(),
                        seen: set[str], request_digest: str, plan_id: str | None = None):
        # Validate the entire batch before executing any request. Duplicate
        # detection is local to this role's current decision conversation.
        keys = []
        for request in requests:
            self._authorize_tool(role, request.name, request.reservation_id)
            key = digest({"name": request.name, "reservation_id": request.reservation_id,
                          "changes": changes if request.name == "validate_candidate_plan" else ()})
            if key in seen or key in keys:
                self.log("core.tool_rejected", "Repeated tool request in the current conversation",
                         agent=self.agent(role))
                raise CoreFailure(ErrorCode.INVALID_INPUT, "REPEATED_TOOL_REQUEST")
            keys.append(key)
        if self.snapshot_fingerprint is None:
            raise CoreFailure(ErrorCode.INVALID_STATE, "TOOL_OBSERVATION_SOURCE_UNAVAILABLE")
        observations = []
        for request in requests:
            result = self.tool(role, request.name, reservation_id=request.reservation_id, changes=changes)
            try:
                observation = {
                    "run_id": self.run_id, "incident_id": self.incident_id,
                    "agent_id": self.agent(role).agent_id, "role": role,
                    "request_digest": request_digest,
                    "tool_request_digest": keys[len(observations)],
                    "tool_name": request.name,
                    "arguments": {"reservation_id": request.reservation_id},
                    "result": self.observation_encoder(request.name, result),
                    "source": {
                        "port": "ReadonlyPort", "method": request.name,
                        "kind": "append_only_audit" if request.name == "append_run_step" else "readonly_run_snapshot",
                        "snapshot_fingerprint": self.snapshot_fingerprint,
                    },
                }
                if request.name == "validate_candidate_plan":
                    observation["bound_plan_id"] = plan_id
                if self.operation == "reservation_change":
                    observation["change_id"] = observation.pop("incident_id")
                    observation["source"]["port"] = "ReservationChangeReadonlyPort"
                    observation["source"]["operation"] = self.operation
                observation["observation_id"] = digest(observation)
                observations.append(observation)
            except CoreFailure:
                raise
            except Exception:
                raise CoreFailure(ErrorCode.WORKFLOW_FAILED, "TOOL_OBSERVATION_INVALID") from None
        seen.update(keys)
        if len(canonical(observations).encode("utf-8")) > MAX_OBSERVATION_BYTES:
            raise CoreFailure(ErrorCode.INVALID_INPUT, "TOOL_OBSERVATION_TOO_LARGE")
        self.check_deadline()
        return observations

    def model(self, role: str, response_type: type[BaseModel], payload: dict, status: RunStatus,
              *, plans: dict[str, tuple[ProposalItem, ...]] | None = None,
              changes: tuple[ProposalItem, ...] = ()):
        agent = self.agent(role)
        prompt = self.prompt_resolver(role, agent.prompt_version)
        contracts = self.tool_contract_builder(agent.allowed_tools, tuple(sorted(self.impacted_ids)))
        schema = canonical(role_response_schema(response_type, contracts, payload))
        messages = (
            ModelMessage(role="system", content=prompt.body),
            ModelMessage(role="user", content=canonical({
                **payload, "allowed_tools": sorted(contracts), "tool_contracts": contracts,
            })),
        )
        seen: set[str] = set()
        for tool_round in range(MAX_TOOL_ROUNDS + 1):
            request = ModelRequest(run_id=self.run_id, agent_id=agent.agent_id,
                                   messages=messages, response_schema=schema)
            parsed = self._model_response(role, response_type, request, status, tool_round=tool_round,
                                          prompt_hash=prompt.body_sha256)
            self.check_deadline()
            if not parsed.tools:
                return parsed
            if tool_round == MAX_TOOL_ROUNDS:
                self.log("core.tool_rejected", "The one permitted observation follow-up did not produce a final decision",
                         agent=agent, inputs=digest(request), outputs=digest(parsed.tools))
                raise CoreFailure(ErrorCode.INVALID_INPUT, "TOOL_ROUND_LIMIT_REACHED")
            bound_changes = changes
            plan_id = payload.get("chosen_plan_id")
            if isinstance(parsed, PlanningDecision):
                plan_id = parsed.plan_id
                if any(tool.name == "validate_candidate_plan" for tool in parsed.tools):
                    if plans is None or plan_id not in plans:
                        raise CoreFailure(ErrorCode.INVALID_INPUT, "TOOL_PLAN_OUT_OF_SCOPE")
                    bound_changes = plans[plan_id]
            observations = self.requested_tools(role, parsed.tools, changes=bound_changes,
                                                seen=seen, request_digest=digest(request), plan_id=plan_id)
            self.log("core.tool_observation", f"role={role};tool_round={tool_round + 1};observations={len(observations)}",
                     agent=agent, inputs=digest(request), outputs=digest(observations))
            messages += (
                ModelMessage(role="assistant", content=canonical(parsed)),
                ModelMessage(role="user", content=canonical({
                    "tool_round": tool_round + 1, "tool_observations": observations,
                    "observation_scope": {"run_id": self.run_id, "incident_id": self.incident_id,
                                          "agent_id": agent.agent_id, "role": role,
                                          "request_digest": digest(request)},
                })),
            )
        raise CoreFailure(ErrorCode.INVALID_STATE, "TOOL_CONVERSATION_INCOMPLETE")

    def _model_response(self, role: str, response_type: type[BaseModel], request: ModelRequest,
                        status: RunStatus, *, tool_round: int, prompt_hash: str):
        agent = self.agent(role)
        if self.client is None:
            raise CoreFailure(ErrorCode.PROVIDER_UNAVAILABLE, "UNCONFIGURED: no model client supplied")
        input_bytes = require_bounded_input(request.model_dump(mode="json"))
        if self.calls >= self.settings.calls_per_run:
            raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "RUN_MODEL_BUDGET_EXCEEDED")
        usage = self.system_read("get_model_usage")
        today = datetime.now(timezone.utc).date().isoformat()
        if usage.utc_date != today:
            raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "MODEL_USAGE_DATE_UNAVAILABLE")
        if usage.daily_model_calls >= self.settings.daily_calls:
            raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "DAILY_MODEL_BUDGET_EXCEEDED")
        request_digest = digest(request)
        self.log("core.model_start", f"Starting authorized {role} request;tool_round={tool_round}", agent=agent,
                 inputs=request_digest,
                 outputs=(f"prompt_version={agent.prompt_version};body_sha256={prompt_hash};"
                          f"input_bytes={input_bytes};input_limit={MAX_MODEL_INPUT_BYTES};"
                          f"task_timeout_s={self.settings.task_timeout_seconds}"), status=status)
        started = perf_counter()
        attempted = False
        error = None
        output = ""
        try:
            # Audit/preflight time is part of the same task deadline. The check
            # happens before even entering the adapter or reserving its quota.
            self.check_deadline()
            attempted = True  # Explicit injected invocations count in their Main-bound test mode.
            response = ModelResponse.model_validate(self.client.complete(request))
            output = digest(response.content)
            output += f";input_tokens={response.input_tokens};output_tokens={response.output_tokens}"
            # Extract usage for accounting, but never accept a late role answer.
            self.check_deadline(attempted=True)
            parsed = response_type.model_validate_json(response.content)
            self.check_deadline(attempted=True)
        except CoreFailure as exc:
            attempted = exc.attempted
            error = exc.error
            raise
        except (ValidationError, ValueError):
            error = ServiceError(code=ErrorCode.WORKFLOW_FAILED, message="MODEL_STRUCTURED_OUTPUT_INVALID")
            raise CoreFailure(error.code, error.message, attempted=True) from None
        except Exception:
            error = ServiceError(code=ErrorCode.PROVIDER_UNAVAILABLE, message="MODEL_CLIENT_FAILED")
            raise CoreFailure(error.code, error.message, attempted=True) from None
        finally:
            self.calls += int(attempted)
            fields = dict(inputs=request_digest, outputs=output,
                          duration_ms=int((perf_counter() - started) * 1000), error=error,
                          model_calls=int(attempted), status=status)
            reason = f"Executed {role} model request" if attempted else "Model request blocked before provider attempt"
            try:
                self.log("core.model", reason, agent=agent, **fields)
            except CoreFailure:
                # Preserve an already-consumed attempt if an older/incompatible
                # Main audit sink rejects the frozen agent. Do not reread live
                # configuration or treat this accounting fallback as success.
                self.log("core.model", f"{reason};started_agent={agent.agent_id};agent_audit_rejected",
                         **fields)
                raise
        self.check_deadline()
        return parsed
