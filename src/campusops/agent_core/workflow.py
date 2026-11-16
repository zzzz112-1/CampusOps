"""LangGraph GearShift engine. No checkpoint/interrupt or approval writes.

Fan-out runs only snapshot candidate reads; audit appends are serialized per run.
Main persists WAIT_APPROVAL and owns all subsequent decisions and revalidation.
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from campusops.agent_core.config import CoreSettings
from campusops.agent_core.errors import CoreFailure
from campusops.agent_core.observations import booking_payload as _booking, equipment_payload as _equipment, incident_payload
from campusops.agent_core.prompts import resolve_prompt
from campusops.agent_core.registry import select_agents
from campusops.agent_core.roles import ImpactReport, PlanningDecision, VerificationReport
from campusops.agent_core.runtime import RunContext, digest
from campusops.agent_core.solver import SearchResult, solve
from campusops.contracts.models import (
    AgentConfig, BookingRuleCatalog, BookingRules, Equipment, ErrorCode, Incident, PlanResult, ProposalItem,
    Reservation, ReservationStatus, ResourceSnapshot, RunStatus, ServiceError, ValidationResult,
)
from campusops.contracts.ports import ModelClientPort, ReadonlyPort


class WorkflowState(TypedDict, total=False):
    run_id: str
    incident_id: str
    snapshot: ResourceSnapshot
    incident: Incident
    impacted: tuple[Reservation, ...]
    rules: BookingRules
    registry_snapshot: tuple[AgentConfig, ...]
    routing_decisions: tuple
    candidate_batches: Annotated[tuple[tuple[str, tuple[Equipment, ...]], ...], operator.add]
    search: SearchResult
    changes: tuple[ProposalItem, ...]
    chosen_plan: str
    planning_reasons: tuple[str, ...]
    validation: ValidationResult
    feedback: tuple[str, ...]
    retry_count: int
    model_call_count: int
    result: PlanResult


class GearShiftOrchestrator:
    def __init__(self, settings: CoreSettings | None = None):
        self.settings = settings or CoreSettings.from_env()

    def run_reservation_change(self, change_id, readonly_port, model_client):
        from campusops.agent_core.reservation_change import run_change
        return run_change(self.settings, change_id, readonly_port, model_client)

    def run_incident(self, incident_id: str, run_id: str, readonly_port: ReadonlyPort,
                     model_client: ModelClientPort | None) -> PlanResult:
        context = RunContext(run_id, incident_id, readonly_port, model_client, self.settings)
        try:
            final = self._build_graph(context).invoke({
                "run_id": run_id, "incident_id": incident_id, "candidate_batches": (),
                "retry_count": 0, "model_call_count": 0, "feedback": (),
            }, config={"max_concurrency": self.settings.parallel_queries, "recursion_limit": 64})
            context.check_deadline()
            return final["result"]
        except CoreFailure as exc:
            error = exc.error
        except Exception:
            error = ServiceError(code=ErrorCode.WORKFLOW_FAILED, message="CORE_WORKFLOW_FAILED")
        # Failure to append evidence cannot be turned into a successful plan.
        try:
            context.log("core.failed", "Workflow stopped safely", error=error)
        except CoreFailure:
            error = ServiceError(code=ErrorCode.WORKFLOW_FAILED, message="AUDIT_APPEND_FAILED")
        return PlanResult(run_id=run_id, incident_id=incident_id, status="FAILED",
                          validation=ValidationResult(passed=False, issues=(error.message,)),
                          explanation=error.message, error=error)

    @staticmethod
    def _manual(ctx: RunContext, reason: str, validation: ValidationResult | None = None):
        ctx.log("core.needs_manual", reason)
        return {"result": PlanResult(
            run_id=ctx.run_id, incident_id=ctx.incident_id, status="NEEDS_MANUAL",
            validation=validation if validation and not validation.passed else ValidationResult(passed=False, issues=(reason,)),
            explanation=reason,
        )}

    @staticmethod
    def _evidence(state: WorkflowState) -> str:
        return "snapshot:" + state["snapshot"].fingerprint

    def _known_evidence_ids(self, state: WorkflowState) -> set[str]:
        known = {self._evidence(state), "incident:" + state["incident"].id}
        known.update("reservation:" + row.id for row in state["impacted"])
        # Main's deterministic validator cites the exact snapshot reservation
        # version. Admit only those current versions, never arbitrary suffixes.
        known.update(f"reservation:{row.id}:v{row.version}" for row in state["impacted"])
        known.update("equipment:" + row.id for row in state["snapshot"].equipment)
        rules = state["rules"]
        if isinstance(rules, BookingRuleCatalog):
            known.add(f"ruleset:{rules.ruleset_id}:v{rules.version}")
            known.update(f"rule:{row.rule_id}:v{row.version}" for row in rules.rules)
        return known

    def _model_evidence(self, state: WorkflowState) -> dict:
        return {
            "required_reservation_ids": sorted(row.id for row in state["impacted"]),
            "required_snapshot_evidence_id": self._evidence(state),
            "allowed_evidence_ids": sorted(self._known_evidence_ids(state)),
        }

    def _evidence_matches(self, state: WorkflowState, report) -> bool:
        ids = tuple(row.id for row in state["impacted"])
        known = self._known_evidence_ids(state)
        return (len(report.reservation_ids) == len(ids) and set(report.reservation_ids) == set(ids)
                and self._evidence(state) in report.evidence_ids and set(report.evidence_ids) <= known)

    def _build_graph(self, ctx: RunContext):
        def scope(state):
            snapshot = ctx.system_read("get_snapshot")
            incident = ctx.system_read("get_incident", ctx.incident_id)
            impacted = ctx.system_read("get_impacted_reservations", ctx.incident_id)
            rules = ctx.system_read("get_booking_rules")
            ctx.log("core.scope", "Captured immutable input snapshot and impact set",
                    outputs=digest({"snapshot": snapshot, "incident": incident, "impacted": impacted, "rules": rules}),
                    status=RunStatus.SCOPING)
            expected = tuple(sorted((row for row in snapshot.reservations if row.status == ReservationStatus.ACTIVE
                                     and row.equipment_id == incident.equipment_id and row.ends_at > incident.effective_at),
                                    key=lambda row: row.id))
            if (incident.id != ctx.incident_id or incident.status != "OPEN" or incident not in snapshot.incidents
                    or tuple(sorted(impacted, key=lambda row: row.id)) != expected):
                return self._manual(ctx, "INPUT_EVIDENCE_MISMATCH_RESTART_REQUIRED")
            if len(impacted) > 20 or len(snapshot.equipment) > 50:
                return self._manual(ctx, "GEARSHIFT_SCOPE_LIMIT_EXCEEDED")
            ctx.impacted_ids = {row.id for row in impacted}
            ctx.snapshot_fingerprint = snapshot.fingerprint
            base = {"snapshot": snapshot, "incident": incident, "impacted": impacted, "rules": rules}
            if not impacted:
                ctx.log("core.no_impact", "NO_IMPACT: no model calls or candidate queries required")
                return {**base, "result": PlanResult(
                    run_id=ctx.run_id, incident_id=ctx.incident_id, status="NO_IMPACT",
                    validation=ValidationResult(passed=True, evidence_ids=("snapshot:" + snapshot.fingerprint,)),
                    explanation="NO_IMPACT: deterministic scan found no affected reservations; zero model calls",
                )}
            return base

        def route(state):
            configs = ctx.system_read("list_agent_configs")
            routes = select_agents(configs)
            ctx.routes = {row.task.role: row for row in routes}
            for row in routes:
                reason = (f"role={row.task.role};skill={row.task.skill};priority={row.task.priority};"
                          f"dependencies={','.join(row.task.dependencies) or 'none'};"
                          f"eligible={len(row.eligible)};rejected={len(row.rejected)};stable_id_tiebreak")
                ctx.log("core.route", reason, agent=row.selected,
                        inputs=digest(configs), outputs=digest({"eligible": row.eligible, "rejected": row.rejected,
                                                               "selected": row.selected}))
            if any(row.selected is None for row in routes):
                return self._manual(ctx, "REQUIRED_AGENT_CAPABILITY_OR_TOOL_PERMISSION_UNAVAILABLE")
            # Reject unsupported versions before spending even the first role's
            # call; the selected configurations remain fixed for this run.
            for row in routes:
                resolve_prompt(row.task.role, row.selected.prompt_version)
            if ctx.client is None:
                raise CoreFailure(ErrorCode.PROVIDER_UNAVAILABLE, "UNCONFIGURED: no model client supplied")
            return {"registry_snapshot": configs, "routing_decisions": routes}

        def impact(state):
            incident = ctx.tool("impact", "get_incident")
            impacted = ctx.tool("impact", "get_impacted_reservations")
            rules = ctx.tool("impact", "get_booking_rules")
            report = ctx.model("impact", ImpactReport, {
                "incident": incident_payload(incident),
                "reservations": [_booking(row) for row in impacted], "rules": rules.model_dump(mode="json"),
                "evidence_id": self._evidence(state),
                **self._model_evidence(state),
            }, RunStatus.SCOPING)
            if not self._evidence_matches(state, report):
                return self._manual(ctx, "INSUFFICIENT_IMPACT_EVIDENCE")
            ctx.completed.add("impact")
            return {"model_call_count": ctx.calls}

        def fan_out(state):
            ctx.check_deadline()
            if "result" in state:
                return END
            count = len(state["impacted"])
            mode = ("parallel_fan_out" if count > 1 and self.settings.parallel_queries > 1
                    else "serial_fan_out" if count > 1 else "single_task")
            ctx.log("core.candidate_dispatch", f"{mode};tasks={count};concurrency_limit={self.settings.parallel_queries}",
                    status=RunStatus.PLANNING)
            return [Send("query_candidates", {"reservation_id": row.id}) for row in state["impacted"]]

        def query_candidates(task):
            rows = ctx.tool("planner", "list_equipment_candidates", reservation_id=task["reservation_id"])
            return {"candidate_batches": ((task["reservation_id"], rows),)}

        def search(state):
            ctx.check_deadline()
            batches = dict(state["candidate_batches"])
            if set(batches) != ctx.impacted_ids or len(state["candidate_batches"]) != len(batches):
                return self._manual(ctx, "CANDIDATE_GATHER_INCOMPLETE")
            result = solve(state["snapshot"], state["impacted"], batches, self.settings)
            ctx.check_deadline()
            ctx.log("core.solve", f"Bounded global search;visited={result.visited};plans={len(result.plans)};exhausted={result.exhausted}",
                    outputs=digest(result.plans))
            if not result.plans:
                return self._manual(ctx, "NO_FEASIBLE_PLAN" if result.exhausted else "SEARCH_LIMIT_REACHED_NO_PLAN_PROVEN")
            return {"search": result}

        def planner(state):
            report = ctx.model("planner", PlanningDecision, {
                "reservations": [_booking(row) for row in state["impacted"]],
                "candidate_evidence": {key: [_equipment(row) for row in rows] for key, rows in sorted(state["candidate_batches"])},
                "offered_plans": [{"plan_id": row.plan_id, "rank": index, "score": row.score,
                                   "changes": [item.model_dump(mode="json") for item in row.changes]}
                                  for index, row in enumerate(state["search"].plans)],
                "feedback": state["feedback"], "retry_count": ctx.retry,
                "previous_plan_id": state.get("chosen_plan"),
                "hard_rejection_codes": [row.code.value for row in state["validation"].violations]
                    if "validation" in state else [],
                "evidence_id": self._evidence(state), "rules": state["rules"].model_dump(mode="json"),
            }, RunStatus.PLANNING,
                plans={row.plan_id: row.changes for row in state["search"].plans})
            chosen = next((row for row in state["search"].plans if row.plan_id == report.plan_id), None)
            if chosen is None:
                validation = ValidationResult(passed=False, issues=("MODEL_SELECTED_UNOFFERED_PLAN",))
                ctx.log("core.plan_rejected", "Rejected a model plan identifier absent from deterministic offers")
            else:
                validation = ctx.tool("planner", "validate_candidate_plan", changes=chosen.changes)
            if validation.passed:
                ctx.completed.add("planner")
            elif any(row.code == ErrorCode.SNAPSHOT_CONFLICT for row in validation.violations):
                return self._manual(ctx, "SNAPSHOT_CHANGED_RESTART_REQUIRED", validation)
            return {"changes": chosen.changes if chosen else (), "chosen_plan": chosen.plan_id if chosen else "",
                    "planning_reasons": report.reason_codes, "validation": validation, "model_call_count": ctx.calls,
                    "feedback": () if validation.passed else ("HARD_VALIDATION_REJECTED",)}

        def verifier(state):
            # Read the evidence again under the independently selected verifier's tool permissions.
            ctx.tool("verifier", "get_incident")
            impacted = ctx.tool("verifier", "get_impacted_reservations")
            rules = ctx.tool("verifier", "get_booking_rules")
            independent = {row.id: ctx.tool("verifier", "list_equipment_candidates", reservation_id=row.id)
                           for row in impacted}
            validation = ctx.tool("verifier", "validate_candidate_plan", changes=state["changes"])
            if not validation.passed:
                if any(row.code == ErrorCode.SNAPSHOT_CONFLICT for row in validation.violations):
                    return self._manual(ctx, "SNAPSHOT_CHANGED_RESTART_REQUIRED", validation)
                return {"validation": validation, "feedback": ("INDEPENDENT_HARD_VALIDATION_REJECTED",)}
            report = ctx.model("verifier", VerificationReport, {
                "changes": [item.model_dump(mode="json") for item in state["changes"]],
                "reservations": [_booking(row) for row in impacted],
                "candidate_evidence": {key: [_equipment(row) for row in rows] for key, rows in independent.items()},
                "validation": validation.model_dump(mode="json"), "rules": rules.model_dump(mode="json"),
                "evidence_id": self._evidence(state), "planner_reason_codes": state["planning_reasons"],
                "chosen_plan_id": state["chosen_plan"],
                **self._model_evidence(state),
            }, RunStatus.VERIFYING, changes=state["changes"])
            if not self._evidence_matches(state, report):
                return self._manual(ctx, "INSUFFICIENT_VERIFIER_EVIDENCE")
            if report.verdict == "needs_manual":
                return self._manual(ctx, "VERIFIER_REQUESTED_MANUAL_REVIEW")
            if report.verdict == "revise":
                return {"validation": validation, "feedback": report.reason_codes, "model_call_count": ctx.calls}
            ctx.completed.add("verifier")
            ctx.log("core.plan_ready", "Hard validation and independent verifier passed; returning to Main for approval",
                    outputs=digest(state["changes"]))
            return {"model_call_count": ctx.calls, "validation": validation, "result": PlanResult(
                run_id=ctx.run_id, incident_id=ctx.incident_id, status="WAIT_APPROVAL", proposal_id=None,
                changes=state["changes"], validation=validation,
                explanation=(f"Planner selected {state['chosen_plan']} from {len(state['search'].plans)} bounded deterministic offers; "
                             f"reasons={','.join(state['planning_reasons'])}. "
                             f"All {len(state['changes'])} affected bookings keep their time windows. "
                             f"Main readonly hard validation and independent verifier passed; "
                             f"model_calls={ctx.calls};replans={ctx.retry};search_complete={state['search'].exhausted}. "
                             "Administrator approval and transactional recheck required."),
            )}

        def replan(state):
            ctx.check_deadline()
            maximum = min(self.settings.max_replans, state["rules"].max_replans)
            if ctx.retry >= maximum:
                return self._manual(ctx, "REPLAN_LIMIT_REACHED", state["validation"])
            if ctx.calls >= self.settings.calls_per_run:
                raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "RUN_MODEL_BUDGET_EXCEEDED")
            ctx.retry += 1
            ctx.completed.discard("planner")
            ctx.completed.discard("verifier")
            ctx.log("core.replan", "Validation feedback returns only to the planning node",
                    outputs=digest(state["feedback"]), status=RunStatus.REPLANNING)
            return {"retry_count": ctx.retry}

        graph = StateGraph(WorkflowState)
        for name, node in (("scope", scope), ("route", route), ("impact", impact),
                           ("query_candidates", query_candidates), ("solve", search),
                           ("planner", planner), ("verifier", verifier), ("replan", replan)):
            graph.add_node(name, node)
        graph.add_edge(START, "scope")
        graph.add_conditional_edges("scope", lambda state: END if "result" in state else "route", [END, "route"])
        graph.add_conditional_edges("route", lambda state: END if "result" in state else "impact", [END, "impact"])
        graph.add_conditional_edges("impact", fan_out, [END, "query_candidates"])
        graph.add_edge("query_candidates", "solve")
        graph.add_conditional_edges("solve", lambda state: END if "result" in state else "planner", [END, "planner"])
        graph.add_conditional_edges("planner", lambda state: END if "result" in state else
                                    "verifier" if state["validation"].passed else "replan", [END, "verifier", "replan"])
        graph.add_conditional_edges("verifier", lambda state: END if "result" in state else "replan", [END, "replan"])
        graph.add_conditional_edges("replan", lambda state: END if "result" in state else "planner", [END, "planner"])
        return graph.compile()
