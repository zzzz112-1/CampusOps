"""UI commands and transient state; all business persistence belongs to Main."""

from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import (
    ApplyResult, ApplyStatus, ErrorCode, IncidentCreate, Proposal, ProposalStatus,
    Reservation, RunStatus, WorkflowRun,
    EquipmentRecoveryCreate,
)
from campusops.contracts.ports import ApplicationPort, IdempotentWorkflowPort, EquipmentMaintenancePort

RESET_CONFIRMATION = "RESET_FICTIONAL_DEMO"
INPUT_TZ = ZoneInfo("Asia/Shanghai")


class UiInputError(ValueError):
    """A safe, user-facing input error, without echoing the input."""


def beijing_effective_at(day: date, clock: time) -> datetime:
    """The UI's explicitly labelled wall-clock controls produce aware UTC input."""
    if not isinstance(day, date) or isinstance(day, datetime) or not isinstance(clock, time):
        raise UiInputError("请选择完整的故障日期和时间（北京时间）。")
    if clock.tzinfo is not None:
        raise UiInputError("请使用北京时间控件选择时间。")
    return datetime.combine(day, clock, tzinfo=INPUT_TZ).astimezone(timezone.utc)


def parse_effective_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError):
        raise UiInputError("生效时间必须为含时区的 ISO 8601 时间，例如 2026-11-10T08:00:00+08:00。") from None
    return parsed.astimezone(timezone.utc)


def can_decide(run: WorkflowRun, proposal: Proposal | None) -> bool:
    return bool(
        run.status == RunStatus.WAIT_APPROVAL
        and proposal is not None
        and proposal.run_id == run.id
        and proposal.incident_id == run.incident_id
        and run.result is not None
        and run.result.proposal_id == proposal.id
        and proposal.status == ProposalStatus.VALIDATED
        and proposal.validation.passed
        and proposal.changes
    )


@dataclass(frozen=True)
class DecisionView:
    run: WorkflowRun
    proposal: Proposal | None
    reservations: tuple[Reservation, ...]


@dataclass(frozen=True)
class DecisionOutcome:
    result: ApplyResult
    current: DecisionView


class UiController:
    def __init__(self, service: ApplicationPort, state: MutableMapping[str, Any], *, read_only: bool = False):
        self.service = service
        self.state = state
        self.read_only = read_only

    def _write_allowed(self):
        if self.read_only:
            raise UiInputError("当前为只读回看，未执行登记、生成、审批或重置。")

    def attempted(self, intent: tuple) -> bool:
        return intent in self.state.get("campusops_command_attempts", {})

    def _begin(self, intent: tuple):
        """Consume before dispatch, including when the response or readback is lost.

        This is a session guard, not a durable service idempotency guarantee.
        A new intent always requires another explicit user action.
        """
        self._write_allowed()
        attempts = dict(self.state.get("campusops_command_attempts", {}))
        if intent in attempts:
            raise UiInputError("此请求已经提交，不能直接重复发送。请先刷新并核对持久记录；结果不明不表示成功。")
        attempts[intent] = "unconfirmed"
        self.state["campusops_command_attempts"] = attempts

    def _confirmed(self, intent: tuple):
        attempts = dict(self.state["campusops_command_attempts"])
        attempts[intent] = "read_back"
        self.state["campusops_command_attempts"] = attempts

    def decision_generation(self, proposal_id: str) -> int:
        return self.state.get("campusops_decision_generations", {}).get(proposal_id, 0)

    def _decision_intent(self, proposal_id: str) -> tuple:
        return ("decision", proposal_id, self.decision_generation(proposal_id))

    def decision_attempted(self, proposal_id: str) -> bool:
        return self.attempted(self._decision_intent(proposal_id))

    def prepare_decision_retry(self, run_id: str):
        """Explicit acknowledgement only; never sends an approval/rejection."""
        self._write_allowed()
        current = self._pending(run_id)
        proposal_id = current.proposal.id
        if not self.decision_attempted(proposal_id):
            raise UiInputError("当前没有需要重新确认的审批请求。")
        generations = dict(self.state.get("campusops_decision_generations", {}))
        generations[proposal_id] = self.decision_generation(proposal_id) + 1
        self.state["campusops_decision_generations"] = generations

    def register_failure(self, equipment_id: str, effective_at: str, reason: str, *, confirmed: bool,
                         submission_id: str | None = None):
        self._write_allowed()
        if not confirmed:
            raise UiInputError("请先确认登记故障；此操作将标记器材故障，保留原预约。")
        try:
            command = IncidentCreate(
                equipment_id=equipment_id, effective_at=parse_effective_at(effective_at), reason=reason.strip(),
            )
        except ValidationError:
            raise UiInputError("请选择器材，并填写 1–500 字的故障原因。") from None
        identity = submission_id if submission_id is not None else uuid5(NAMESPACE_URL, command.model_dump_json()).hex
        intent = ("register", identity)
        self._begin(intent)
        incident = self.service.register_equipment_failure(command)
        persisted = next((row for row in self.service.list_incidents() if row.id == incident.id), None)
        if (persisted != incident or incident.equipment_id != command.equipment_id
                or incident.effective_at != command.effective_at or incident.reason != command.reason):
            raise CampusOpsError(ErrorCode.INVALID_STATE, "事故登记结果尚未从持久列表核实，请刷新后检查。")
        self._confirmed(intent)
        self.state["campusops_incident_id"] = incident.id
        return incident

    def start_workflow(self, incident_id: str, *, generation: int, previous_run_id: str | None = None) -> WorkflowRun:
        intent = ("workflow", incident_id, generation)
        self._begin(intent)
        if isinstance(self.service, IdempotentWorkflowPort):
            result = self.service.start_workflow_once(incident_id, self.workflow_key(incident_id, previous_run_id))
        else:
            result = self.service.start_workflow(incident_id)
        saved = self.service.get_run(result.run_id)
        in_progress = (
            result.status == "FAILED" and result.error is not None and result.error.code == ErrorCode.INVALID_STATE
            and result.error.message.startswith("WORKFLOW_ALREADY_IN_PROGRESS:")
        )
        pending_states = (RunStatus.CREATED, RunStatus.SCOPING, RunStatus.PLANNING, RunStatus.VERIFYING,
                          RunStatus.REPLANNING, RunStatus.APPLYING)
        # The optional port's progress notice is not the persisted planning result.
        # It may finish between the port response and this read; show that actual state.
        verified_result = (
            saved.result is not None and saved.result.run_id == saved.id and saved.result.incident_id == incident_id
            if in_progress else saved.result is not None and saved.result == result
        )
        verified_pending = in_progress and saved.result is None and saved.status in pending_states
        if (result.incident_id != incident_id or saved.id != result.run_id
                or saved.incident_id != incident_id or not (verified_result or verified_pending)):
            raise CampusOpsError(ErrorCode.INVALID_STATE, "生成结果尚未从对应持久运行核实，请刷新后检查。")
        self._confirmed(intent)
        return saved

    @staticmethod
    def workflow_key(incident_id: str, previous_run_id: str | None = None) -> str:
        """Same logical request across sessions; only a persisted predecessor advances it."""
        identity = ("campusops-workflow-v1", incident_id, previous_run_id)
        return "ui_workflow_" + uuid5(NAMESPACE_URL, repr(identity)).hex

    def read_decision(self, run_id: str) -> DecisionView:
        run = self.service.get_run(run_id)
        proposal_id = run.result.proposal_id if run.result else None
        proposal = self.service.get_proposal(proposal_id) if proposal_id else None
        return DecisionView(run, proposal, self.service.list_reservations())

    def approval_key(self, proposal_id: str, actor_alias: str) -> str:
        actor = self._actor(actor_alias)
        # Deterministic across reruns AND new browser sessions. No raw alias in the key.
        identity = (proposal_id, actor)
        keys = dict(self.state.get("campusops_approval_keys", {}))
        keys.setdefault(identity, "ui_approve_" + uuid5(NAMESPACE_URL, repr(("campusops-v1", *identity))).hex)
        self.state["campusops_approval_keys"] = keys
        return keys[identity]

    @staticmethod
    def _actor(value: str) -> str:
        actor = value.strip()
        if not 1 <= len(actor) <= 100:
            raise UiInputError("请填写 1–100 字的操作人匿名别名。")
        return actor

    def _pending(self, run_id: str) -> DecisionView:
        current = self.read_decision(run_id)
        if not can_decide(current.run, current.proposal):
            raise CampusOpsError(ErrorCode.INVALID_STATE, "当前持久状态不允许审批，请刷新运行与方案。")
        return current

    def approve(self, run_id: str, actor_alias: str) -> DecisionOutcome:
        self._write_allowed()
        actor = self._actor(actor_alias)
        before = self._pending(run_id)
        proposal = before.proposal
        assert proposal is not None
        intent = self._decision_intent(proposal.id)
        self._begin(intent)
        result = self.service.approve_proposal(proposal.id, actor, self.approval_key(proposal.id, actor))
        current = self.read_decision(run_id)
        if result.proposal_id != proposal.id:
            raise CampusOpsError(ErrorCode.INVALID_STATE, "审批返回的方案不匹配，结果未确认。")
        if result.status == ApplyStatus.APPLIED:
            persisted = {row.id: row for row in current.reservations}
            verified = (
                current.run.status == RunStatus.APPLIED
                and current.proposal is not None
                and current.proposal.status == ProposalStatus.APPLIED
                and all(
                    (row := persisted.get(item.reservation_id)) is not None
                    and row.equipment_id == item.to_equipment_id
                    and row.starts_at == item.starts_at and row.ends_at == item.ends_at
                    for item in proposal.changes
                )
            )
            if not verified:
                raise CampusOpsError(ErrorCode.INVALID_STATE, "审批返回完成，但持久状态未核实。请刷新后检查，勿直接重复操作。")
            decision = self.service.get_approval_decision(proposal.id)
            if decision is None or decision.proposal_id != proposal.id or decision.decision != "APPROVE":
                raise CampusOpsError(ErrorCode.INVALID_STATE, "批准决定尚未从持久审批记录核实，请刷新后检查。")
        self._confirmed(intent)
        return DecisionOutcome(result, current)

    def reject(self, run_id: str, actor_alias: str, reason: str) -> DecisionOutcome:
        self._write_allowed()
        actor = self._actor(actor_alias)
        reason = reason.strip()
        if not 1 <= len(reason) <= 500:
            raise UiInputError("请填写 1–500 字的拒绝原因。")
        before = self._pending(run_id)
        assert before.proposal is not None
        intent = self._decision_intent(before.proposal.id)
        self._begin(intent)
        result = self.service.reject_proposal(before.proposal.id, actor, reason)
        current = self.read_decision(run_id)
        if result.proposal_id != before.proposal.id or (result.status == ApplyStatus.REJECTED and (
            current.run.status != RunStatus.REJECTED
            or current.proposal is None or current.proposal.status != ProposalStatus.REJECTED
        )):
            raise CampusOpsError(ErrorCode.INVALID_STATE, "拒绝结果尚未从持久状态核实，请刷新后检查。")
        if result.status == ApplyStatus.REJECTED:
            decision = self.service.get_approval_decision(before.proposal.id)
            if (decision is None or decision.proposal_id != before.proposal.id or decision.decision != "REJECT"
                    or current.reservations != before.reservations):
                raise CampusOpsError(ErrorCode.INVALID_STATE, "拒绝决定或预约保持情况尚未核实，请刷新后检查。")
        self._confirmed(intent)
        return DecisionOutcome(result, current)

    def reset_demo(self, phrase: str, *, second_confirmation: bool, target_allowed: bool):
        self._write_allowed()
        if not target_allowed or not self.state.get("campusops_reset_pending"):
            raise UiInputError("请先在本工作树的虚构演示库上发起重置。")
        if not second_confirmation or phrase != RESET_CONFIRMATION:
            raise UiInputError("请完成第二次确认并准确输入 RESET_FICTIONAL_DEMO。")
        self.service.reset_demo_data(RESET_CONFIRMATION)
        # Read through the port after the command, before reporting completion.
        self.service.list_equipment()
        self.service.list_reservations()
        self.service.list_runs()
        for key in ("campusops_incident_id", "campusops_run_id", "campusops_approval_keys", "campusops_reset_pending",
                    "campusops_command_attempts", "campusops_decision_generations",
                    "campusops_last_incident_id", "campusops_decision_feedback", "incident_picker", "workflow_run_picker", "trace_run_picker"):
            self.state.pop(key, None)
        # A reset replaces the fictional business records. Old previews/results
        # must not remain actionable or masquerade as records in the new data.
        for key in tuple(self.state):
            if key.startswith(("campusops_booking_", "booking_", "catalog_")):
                self.state.pop(key, None)

    def recover_equipment(self, equipment_id, expected_version, actor_alias, note, *, confirmed):
        self._write_allowed()
        if not confirmed or not note.strip():
            raise UiInputError("请填写维修说明，并确认器材已完成维修和功能检查。")
        if not isinstance(self.service, EquipmentMaintenancePort):
            raise UiInputError("当前服务尚未提供维修恢复功能，请联系维护者。")
        actor = self._actor(actor_alias)
        try:
            request = EquipmentRecoveryCreate(
                equipment_id=equipment_id, expected_equipment_version=expected_version,
                actor_alias=actor, note=note.strip(),
                idempotency_key="ui_recovery_" + uuid5(NAMESPACE_URL, repr((equipment_id, expected_version, actor, note.strip()))).hex,
            )
        except ValidationError:
            raise UiInputError("请填写 1–500 字的维修说明，并确认器材状态与操作人别名。") from None
        intent = ("recovery", equipment_id, expected_version)
        self._begin(intent)
        before = self.service.list_reservations()
        result = self.service.recover_equipment(request)
        current = next((row for row in self.service.list_equipment() if row.id == equipment_id), None)
        incidents = self.service.list_incidents()
        if (result.equipment != current or current.status != "AVAILABLE"
                or current.version != expected_version + 1 or self.service.list_reservations() != before
                or any(row.equipment_id == equipment_id and row.status == "OPEN" for row in incidents)):
            raise UiInputError("维修结果尚未从持久记录核实，请刷新核对，不要重复提交。")
        self._confirmed(intent)
        self.state.pop("campusops_last_incident_id", None)
        return result
