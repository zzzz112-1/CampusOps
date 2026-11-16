"""Main-owned bounded workflow persistence; no booking writes in this module."""
from hashlib import sha256
import json

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import (
    AgentConfig, ErrorCode, ExecutionMode, ModelUsage, ReservationChangeContext, ReservationChangeCreate,
    ReservationChangePlanResult, ReservationChangePolicy, ReservationChangeRun, RunStep, RunStepCreate,
    ServiceError, ValidationResult,
)
from campusops.contracts.ports import ReservationChangeOrchestrationPort
from campusops.domain.reservations import requested_booking, validate_change, change_items, change_candidate_diagnostics, CHANGE_CANDIDATE_POLICY
from campusops.services.readonly import ACTIVE_STEP_STATUSES
from campusops.storage import repository as repo
from campusops.storage import reservation_events as events


class ReservationChangeTools:
    def __init__(self, database, context, snapshot, agents):
        self.__database, self.__context, self.__snapshot, self.__agents = database, context, snapshot, agents

    def get_incident(self, incident_id):
        # Compatibility tool name, explicitly typed as reservation_change. No
        # fake equipment fault is created or returned, and no equipment is changed.
        if incident_id != self.__context.id:
            raise CampusOpsError(ErrorCode.INVALID_INPUT, "工具不能访问其他修改请求。")
        return self.__context

    def get_impacted_reservations(self, incident_id):
        self.get_incident(incident_id)
        return (self.__context.before,)

    def list_equipment_candidates(self, reservation_id):
        if reservation_id != self.__context.before.id:
            raise CampusOpsError(ErrorCode.INVALID_INPUT, "工具不能查询其他预约。")
        target = self.__context.request.requested_equipment_id
        return tuple(row for row in self.__snapshot.equipment if (target is None or row.id == target)
                     and validate_change(self.__snapshot, self.__context, requested_booking(self.__context, row.id)).passed)

    def get_booking_rules(self):
        return ReservationChangePolicy()

    def get_snapshot(self):
        return self.__snapshot

    def list_agent_configs(self):
        return self.__agents

    def get_model_usage(self):
        today = repo.now_utc().date().isoformat()
        with self.__database.connect(readonly=True) as connection:
            count = connection.execute("SELECT COALESCE(SUM(model_calls),0) FROM run_steps WHERE execution_mode='real_api' AND substr(created_at_utc,1,10)=?", (today,)).fetchone()[0]
            count += events.extra_model_calls(connection, today)
        return ModelUsage(utc_date=today, daily_model_calls=count)

    def validate_candidate_plan(self, changes):
        changes = tuple(changes)
        if len(changes) != 1 or changes != change_items(self.__context, changes[0].to_equipment_id):
            return ValidationResult(passed=False, issues=("修改方案不符合当前单预约请求。",))
        return validate_change(self.__snapshot, self.__context, requested_booking(self.__context, changes[0].to_equipment_id))

    def append_run_step(self, step):
        step = RunStepCreate.model_validate(step)
        if step.run_id != self.__context.id or step.status is not None and step.status not in ACTIVE_STEP_STATUSES:
            raise CampusOpsError(ErrorCode.INVALID_STATE, "不能修改其他运行或伪造最终状态。")
        if step.agent_id is not None:
            agent = next((row for row in self.__agents if row.agent_id == step.agent_id), None)
            if agent is None or not agent.enabled or step.tool_name and step.tool_name not in agent.allowed_tools:
                raise CampusOpsError(ErrorCode.INVALID_STATE, "此角色或工具未在冻结配置中启用。")
        with self.__database.transaction() as connection:
            current = events.get_change(connection, self.__context.id)
            if current.status not in ({"CREATED"} | ACTIVE_STEP_STATUSES):
                raise CampusOpsError(ErrorCode.INVALID_STATE, "不能为已结束的修改请求追加步骤。")
            record = RunStep(**step.model_dump(), id=repo.new_id("step"), seq=len(current.steps) + 1,
                             execution_mode=current.execution_mode, created_at=repo.now_utc())
            events.change_event(connection, current.id, "RESERVATION_CHANGE_STEP", {"step": record.model_dump(mode="json")})
            return record


def failed(change_id, code, message):
    return ReservationChangePlanResult(change_id=change_id, status="FAILED", validation=ValidationResult(passed=False, issues=(message,)),
                                       explanation=message, error=ServiceError(code=code, message=message))


def plan_change(application, request):
    request = ReservationChangeCreate.model_validate(request)
    database = application._database
    change_id = "change_" + events.key_digest(request.idempotency_key)
    request_hash = sha256(request.model_dump_json().encode("utf-8")).hexdigest()
    with database.transaction() as connection:
        existing = connection.execute("SELECT after_json FROM audit_log WHERE action='RESERVATION_CHANGE_CAPTURED' AND entity_id=?", (change_id,)).fetchone()
        if existing:
            if json.loads(existing[0])["request_hash"] != request_hash:
                raise CampusOpsError(ErrorCode.IDEMPOTENCY_CONFLICT, "此请求标识已经用于不同的预约修改。")
            return events.get_change(connection, change_id)
        snapshot = events.change_snapshot(connection, database)
        before = next((row for row in snapshot.reservations if row.id == request.reservation_id), None)
        if before is None or before.status != "ACTIVE" or before.version != request.expected_reservation_version:
            raise CampusOpsError(ErrorCode.SNAPSHOT_CONFLICT, "原预约已变化或已取消，请刷新后重新填写。")
        for active in events.list_changes(connection, before.id):
            if active.status in ({"CREATED", "WAIT_APPROVAL"} | ACTIVE_STEP_STATUSES):
                previous = active.context.request.model_dump(exclude={"idempotency_key"})
                if previous == request.model_dump(exclude={"idempotency_key"}):
                    return active
                raise CampusOpsError(ErrorCode.INVALID_STATE, "此预约已有处理中或待审批的修改，请先查看并处理原请求。")
        context = ReservationChangeContext(id=change_id, before=before, request=request, snapshot_fingerprint=snapshot.fingerprint)
        if requested_booking(context, request.requested_equipment_id or before.equipment_id) == before and request.requested_equipment_id is not None:
            raise CampusOpsError(ErrorCode.INVALID_INPUT, "尚未修改任何预约内容，无需生成方案。")
        agents = repo.list_agent_configs(connection)
        candidate_validation=change_candidate_diagnostics(snapshot,context)
        run = ReservationChangeRun(id=change_id, context=context, status="CREATED", execution_mode=application._execution_mode,
            provider=application._provider, model_id=application._model_id, created_at=repo.now_utc())
        events.change_event(connection, change_id, "RESERVATION_CHANGE_CAPTURED", {
            "request_hash": request_hash, "run": run.model_dump(mode="json"),
            "policy": ReservationChangePolicy().model_dump(mode="json"), "candidate_policy":CHANGE_CANDIDATE_POLICY,
            "candidate_validation":candidate_validation.model_dump(mode="json"),
            "agents": [row.model_dump(mode="json") for row in agents]}, actor_type="administrator")
    tools = ReservationChangeTools(database, context, snapshot, agents)
    if not isinstance(application._orchestration, ReservationChangeOrchestrationPort):
        result = failed(change_id, ErrorCode.NOT_IMPLEMENTED, "预约修改的三角色调度尚未接入。")
    elif application._execution_mode == ExecutionMode.REAL_API and application._model_client is None:
        result = failed(change_id, ErrorCode.PROVIDER_UNAVAILABLE, "真实模型尚未启用或配置不可用，原预约未修改。")
    elif application._execution_mode not in (ExecutionMode.REAL_API, ExecutionMode.OFFLINE_MOCK_TEST, ExecutionMode.FAILURE_INJECTION_TEST):
        result = failed(change_id, ErrorCode.INVALID_STATE, "必须明确声明真实模型或离线测试执行模式。")
    else:
        try:
            result = ReservationChangePlanResult.model_validate(application._orchestration.run_reservation_change(change_id, tools, application._model_client))
            if result.change_id != change_id:
                result = failed(change_id, ErrorCode.INVALID_PROPOSAL, "调度器返回了其他请求的结果。")
        except CampusOpsError as error:
            result = failed(change_id, error.error.code, "预约修改调度未完成，原预约保持不变。")
        except Exception:
            result = failed(change_id, ErrorCode.WORKFLOW_FAILED, "预约修改调度异常或返回格式无效，原预约保持不变。")
    with database.transaction() as connection:
        status = result.status
        if result.status != "WAIT_APPROVAL" and not candidate_validation.passed:
            # Preserve the actual role/provider outcome, with Main's reasons
            # from the immutable pre-run snapshot. Never recompute old history.
            result=result.model_copy(update={"validation":ValidationResult(passed=False,
                issues=tuple(dict.fromkeys((*candidate_validation.issues,*result.validation.issues))),
                violations=(*candidate_validation.violations,*result.validation.violations),
                evidence_ids=tuple(dict.fromkeys((*candidate_validation.evidence_ids,*result.validation.evidence_ids))))})
        if result.status == "WAIT_APPROVAL":
            try:
                current = events.change_snapshot(connection, database)
            except CampusOpsError:
                result, status = failed(change_id, ErrorCode.INVALID_STATE, "当前规则不可用，原预约未修改。"), "FAILED"
            else:
                validation = validate_change(current, context, result.after)
                if current.fingerprint != snapshot.fingerprint:
                    result, status = failed(change_id, ErrorCode.SNAPSHOT_CONFLICT, "规划期间资源已变化，请重新生成。"), "STALE"
                elif not validation.passed:
                    result = failed(change_id, ErrorCode.INVALID_PROPOSAL, "修改方案未通过Main硬约束校验。")
                    status = "FAILED"
                else:
                    result = result.model_copy(update={"validation": validation})
        events.change_event(connection, change_id, "RESERVATION_CHANGE_RESULT", {"result": result.model_dump(mode="json"), "status": status, "at": repo.timestamp()})
        return events.get_change(connection, change_id)
