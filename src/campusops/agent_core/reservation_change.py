"""Single-booking change graph reusing the real roles, tools and budget path.

No fault is fabricated; no booking is saved here. Main owns final validation,
human approval and the only reservation-writing transaction.
"""
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

from campusops.agent_core.errors import CoreFailure
from campusops.agent_core.observations import booking_payload, equipment_payload, tool_result_payload
from campusops.agent_core.prompts import Prompt, _COMMON
from campusops.agent_core.registry import select_agents
from campusops.agent_core.roles import ImpactReport, PlanningDecision, VerificationReport, tool_contracts
from campusops.agent_core.runtime import RunContext, digest
from campusops.contracts.models import (
    ErrorCode, Reservation, ReservationChangeContext, ReservationChangePlanResult,
    RunStatus, ServiceError, ProposalItem, ValidationResult,
)


def resolve_change_prompt(role, version):
    if version not in ("v1", "v2") or role not in ("impact", "planner", "verifier"):
        raise CoreFailure(ErrorCode.INVALID_INPUT, "UNKNOWN_PROMPT_VERSION_OR_ROLE")
    base = ("You are the " + role + " role for a user-authorized SINGLE RESERVATION CHANGE, not an equipment fault. "
        "The existing reservation ID must be retained. Only the exact requested time window and capabilities are permitted. "
        "Other reservations must stay unchanged. Faulted or unavailable equipment, capability mismatch and overlapping active bookings are forbidden. "
        "Do not approve, write, cancel, invent plans or move any other booking. Human approval and Main transaction validation are mandatory. ")
    instruction = {
        "impact": "Check the original reservation and requested change. Copy required_reservation_ids and required_snapshot_evidence_id exactly, using only allowed_evidence_ids. ",
        "planner": "Select exactly one offered plan_id. Prefer stable rank and the original equipment when it meets the requested change. Never invent a plan or propose another time. Do not use preserve_times as a reason if the request changes the time. ",
        "verifier": "Independently check requested times, candidate equipment, capabilities, conflicts and actual validation. Copy exact required reservation and snapshot evidence IDs. Use pass only with consistent complete evidence and passed hard validation; otherwise revise or needs_manual. ",
    }[role]
    variant = "Explain the evidence concisely. " if version == "v1" else "Use an explicit evidence checklist, resolve only missing facts with allowed tools, and revise your decision after observations. "
    return Prompt(role, version, f"Reservation change prompt {version}. " + base + instruction + variant + _COMMON)


def context_payload(context):
    # Anonymous borrower/actor aliases and free-form reason are not sent to models.
    return {"id": context.id, "kind": context.kind, "before": booking_payload(context.before),
        "requested": {"starts_at": context.request.starts_at.isoformat(), "ends_at": context.request.ends_at.isoformat(),
                      "required_capabilities": list(context.request.required_capabilities),
                      "equipment_id": context.request.requested_equipment_id},
        "borrower_alias_changed": context.request.borrower_alias.strip() != context.before.borrower_alias,
        "snapshot_fingerprint": context.snapshot_fingerprint}


def change_observation(name, result):
    return context_payload(result) if name == "get_incident" and isinstance(result, ReservationChangeContext) else tool_result_payload(name, result)


def change_tool_contracts(tools, ids):
    result = tool_contracts(tools, ids)
    if "get_incident" in result:
        result["get_incident"]["purpose"] = "Compatibility name: read this reservation CHANGE context, not an equipment fault. No argument."
    if "get_impacted_reservations" in result:
        result["get_impacted_reservations"]["purpose"] = "Read only the one existing reservation this change is authorized to modify."
    return result


def requested_after(context, equipment_id):
    return Reservation(**{**context.before.model_dump(), "equipment_id": equipment_id,
        "borrower_alias": context.request.borrower_alias.strip(), "starts_at": context.request.starts_at,
        "ends_at": context.request.ends_at, "required_capabilities": context.request.required_capabilities})


def items_for(context, equipment_id):
    return (ProposalItem(reservation_id=context.before.id, from_equipment_id=context.before.equipment_id,
        to_equipment_id=equipment_id, starts_at=context.request.starts_at, ends_at=context.request.ends_at,
        expected_reservation_version=context.before.version),)


class ChangeState(TypedDict, total=False):
    context: ReservationChangeContext
    offers: tuple
    chosen: str
    changes: tuple
    validation: ValidationResult
    feedback: tuple
    result: ReservationChangePlanResult


def run_change(settings, change_id, port, client):
    ctx = RunContext(change_id, change_id, port, client, settings)
    ctx.operation = "reservation_change"
    ctx.prompt_resolver, ctx.observation_encoder, ctx.tool_contract_builder = resolve_change_prompt, change_observation, change_tool_contracts

    def manual(message, validation=None):
        return {"result": ReservationChangePlanResult(change_id=change_id, status="NEEDS_MANUAL",
            validation=validation if validation is not None and not validation.passed else ValidationResult(passed=False, issues=(message,)), explanation=message)}

    def evidence(context):
        snapshot = ctx.system_read("get_snapshot")
        allowed = {"snapshot:" + snapshot.fingerprint, "change:" + change_id,
                   "reservation:" + context.before.id, f"reservation:{context.before.id}:v{context.before.version}"}
        allowed.update("equipment:" + row.id for row in snapshot.equipment)
        return {"required_reservation_ids": [context.before.id], "required_snapshot_evidence_id": "snapshot:" + snapshot.fingerprint,
                "allowed_evidence_ids": sorted(allowed), "evidence_id": "snapshot:" + snapshot.fingerprint}

    def matched(context, report):
        known = evidence(context)
        return (report.reservation_ids == (context.before.id,) and known["evidence_id"] in report.evidence_ids
                and set(report.evidence_ids) <= set(known["allowed_evidence_ids"]))

    def scope(state):
        context = ctx.system_read("get_incident", change_id)
        snapshot = ctx.system_read("get_snapshot")
        impacted = ctx.system_read("get_impacted_reservations", change_id)
        if (context.id != change_id or context.kind != "reservation_change" or context.before not in snapshot.reservations
                or context.snapshot_fingerprint != snapshot.fingerprint or impacted != (context.before,)
                or context.request.reservation_id != context.before.id):
            raise CoreFailure(ErrorCode.INVALID_INPUT, "CHANGE_CONTEXT_MISMATCH")
        ctx.impacted_ids, ctx.snapshot_fingerprint = {context.before.id}, context.snapshot_fingerprint
        agents = ctx.system_read("list_agent_configs")
        routes = select_agents(agents)
        ctx.routes = {row.task.role: row for row in routes}
        for row in routes:
            ctx.log("change.route", f"operation=reservation_change;role={row.task.role};eligible={len(row.eligible)}", agent=row.selected)
        if any(row.selected is None for row in routes):
            return manual("必需智能体已停用、能力不足或工具权限不足，未修改预约。")
        for row in routes:
            resolve_change_prompt(row.task.role, row.selected.prompt_version)
        return {"context": context, "feedback": ()}

    def impact(state):
        context = ctx.tool("impact", "get_incident")
        bookings = ctx.tool("impact", "get_impacted_reservations")
        rules = ctx.tool("impact", "get_booking_rules")
        report = ctx.model("impact", ImpactReport, {"operation": "reservation_change", "change": context_payload(context),
            "reservations": [booking_payload(row) for row in bookings], "rules": rules.model_dump(mode="json"), **evidence(context)}, RunStatus.SCOPING)
        if not matched(context, report):
            return manual("影响分析证据不完整，需要人工核对。")
        ctx.completed.add("impact")
        candidates = ctx.tool("planner", "list_equipment_candidates", reservation_id=context.before.id)
        candidates = sorted(candidates, key=lambda row: (row.id != context.before.equipment_id, row.id))
        if not candidates:
            return manual("所请求的时间和能力下没有可用器材，原预约保持不变。")
        return {"offers": tuple((f"plan_{i}", row.id) for i, row in enumerate(candidates))}

    def planner(state):
        context = state["context"]
        plans = {plan_id: items_for(context, equipment_id) for plan_id, equipment_id in state["offers"]}
        rules = ctx.tool("planner", "get_booking_rules") if "get_booking_rules" in ctx.agent("planner").allowed_tools else ctx.system_read("get_booking_rules")
        report = ctx.model("planner", PlanningDecision, {"operation": "reservation_change", "change": context_payload(context),
            "reservations": [booking_payload(context.before)], "rules": rules.model_dump(mode="json"),
            "offered_plans": [{"plan_id": identity, "rank": rank, "changes": [row.model_dump(mode="json") for row in plans[identity]]}
                              for rank, (identity, _) in enumerate(state["offers"])],
            "feedback": state.get("feedback", ()), **evidence(context)}, RunStatus.PLANNING, plans=plans)
        if report.plan_id not in plans:
            return {"validation": ValidationResult(passed=False, issues=("模型选择了不存在的候选方案。",)), "feedback": ("INVALID_PLAN_ID",)}
        changes = plans[report.plan_id]
        validation = ctx.tool("planner", "validate_candidate_plan", changes=changes)
        ctx.completed.add("planner")
        return {"chosen": report.plan_id, "changes": changes, "validation": validation, "feedback": validation.issues}

    def verifier(state):
        context = ctx.tool("verifier", "get_incident")
        independent = ctx.tool("verifier", "list_equipment_candidates", reservation_id=context.before.id)
        validation = ctx.tool("verifier", "validate_candidate_plan", changes=state["changes"])
        if not validation.passed:
            return {"validation": validation, "feedback": validation.issues}
        report = ctx.model("verifier", VerificationReport, {"operation": "reservation_change", "change": context_payload(context),
            "reservations": [booking_payload(context.before)], "changes": [row.model_dump(mode="json") for row in state["changes"]],
            "candidate_evidence": [equipment_payload(row) for row in independent], "validation": validation.model_dump(mode="json"),
            "chosen_plan_id": state["chosen"], **evidence(context)}, RunStatus.VERIFYING, changes=state["changes"])
        if not matched(context, report):
            return manual("独立验证证据不完整，需要人工核对。")
        if report.verdict == "needs_manual":
            return manual("验证智能体要求人工处理，原预约保持不变。")
        if report.verdict == "revise":
            return {"feedback": report.reason_codes}
        ctx.completed.add("verifier")
        after = requested_after(context, state["changes"][0].to_equipment_id)
        return {"result": ReservationChangePlanResult(change_id=change_id, status="WAIT_APPROVAL", after=after,
            validation=validation, explanation=f"三角色已分析并验证本次单预约修改。模型调用 {ctx.calls} 次，重新规划 {ctx.retry} 次。保留原预约编号；人工批准前原预约不变。")}

    def replan(state):
        ctx.check_deadline()
        if ctx.retry >= min(settings.max_replans, port.get_booking_rules().max_replans):
            return manual("已达到重新规划上限，请人工处理。")
        if ctx.calls >= settings.calls_per_run:
            raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "RUN_MODEL_BUDGET_EXCEEDED")
        ctx.retry += 1
        ctx.completed.discard("planner")
        ctx.completed.discard("verifier")
        ctx.log("change.replan", "反馈返回同一预约修改的规划节点", status=RunStatus.REPLANNING)
        return {}

    graph = StateGraph(ChangeState)
    for name, node in (("scope", scope), ("impact", impact), ("planner", planner), ("verifier", verifier), ("replan", replan)):
        graph.add_node(name, node)
    graph.add_edge(START, "scope")
    graph.add_conditional_edges("scope", lambda state: END if "result" in state else "impact", [END, "impact"])
    graph.add_conditional_edges("impact", lambda state: END if "result" in state else "planner", [END, "planner"])
    graph.add_conditional_edges("planner", lambda state: END if "result" in state else "verifier" if state["validation"].passed else "replan", [END, "verifier", "replan"])
    graph.add_conditional_edges("verifier", lambda state: END if "result" in state else "replan", [END, "replan"])
    graph.add_conditional_edges("replan", lambda state: END if "result" in state else "planner", [END, "planner"])
    try:
        final = graph.compile().invoke({}, config={"recursion_limit": 64})
        ctx.check_deadline()
        return final["result"]
    except CoreFailure as error:
        failure = error.error
    except Exception:
        failure = ServiceError(code=ErrorCode.WORKFLOW_FAILED, message="预约修改调度未完成。")
    try:
        ctx.log("change.failed", "预约修改安全停止", error=failure)
    except CoreFailure:
        failure = ServiceError(code=ErrorCode.WORKFLOW_FAILED, message="AUDIT_APPEND_FAILED")
    return ReservationChangePlanResult(change_id=change_id, status="FAILED", validation=ValidationResult(passed=False, issues=(failure.message,)),
                                       explanation=failure.message, error=failure)
