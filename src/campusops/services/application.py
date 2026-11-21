"""UI-facing commands/queries; orchestration is injected through a frozen port."""

from pathlib import Path
from hashlib import sha256
import json

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.configuration import ROLE_TOOL_LIMITS, SUPPORTED_PROMPT_VERSIONS
from campusops.contracts.models import (
    AgentConfigPatch, AgentRuntimeConfigPatch, ErrorCode, ExecutionMode, Incident, IncidentCreate, ModelBudgetSnapshot, PlanResult,
    RunStatus, RunStepCreate, ServiceError, ValidationResult, EquipmentRecoveryCreate, EquipmentRecoveryResult,
)
from campusops.contracts.ports import ModelBudgetPort, ModelClientPort, OrchestrationPort
from campusops.domain.validation import impacted_reservations, validate_plan
from campusops.services.approval import ApprovalService
from campusops.services.maintenance import EquipmentMaintenanceService
from campusops.services.catalog import EquipmentCatalogService
from campusops.storage.catalog import archived_ids
from campusops.services.readonly import ReadonlyTools
from campusops.storage import repository as repo
from campusops.storage.database import Database
from campusops.storage.seed import seed_demo


class ApplicationService:
    def __init__(
        self, database: Database, *, orchestration: OrchestrationPort | None = None,
        model_client: ModelClientPort | None = None, execution_mode: ExecutionMode = ExecutionMode.SYSTEM,
        provider: str | None = None, model_id: str | None = None, seed_asset: Path | None = None,
    ):
        self._database = database
        self._approval = ApprovalService(database)
        self._orchestration = orchestration
        self._model_client = model_client
        self._execution_mode = ExecutionMode(execution_mode)
        self._provider = provider
        self._model_id = model_id
        self._seed_asset = seed_asset

    def list_equipment(self):
        with self._database.connect(readonly=True) as connection:
            return repo.list_equipment(connection)

    def list_reservations(self):
        with self._database.connect(readonly=True) as connection:
            return repo.list_reservations(connection)

    def list_incidents(self):
        with self._database.connect(readonly=True) as connection:
            return repo.list_incidents(connection)

    def list_runs(self):
        with self._database.connect(readonly=True) as connection:
            ids = [row[0] for row in connection.execute("SELECT id FROM workflow_runs ORDER BY created_at_utc,id")]
            return tuple(repo.get_run(connection, run_id) for run_id in ids)

    def list_agent_configs(self):
        with self._database.connect(readonly=True) as connection:
            return repo.list_agent_configs(connection)

    def get_booking_rules(self):
        return self._database.get_booking_rules()

    def list_run_steps(self, run_id: str):
        with self._database.connect(readonly=True) as connection:
            repo.get_run(connection, run_id)
            return repo.list_run_steps(connection, run_id)

    def get_run(self, run_id: str):
        with self._database.connect(readonly=True) as connection:
            return repo.get_run(connection, run_id)

    def get_proposal(self, proposal_id: str):
        with self._database.connect(readonly=True) as connection:
            return repo.get_proposal(connection, proposal_id)

    def get_approval_decision(self, proposal_id: str):
        with self._database.connect(readonly=True) as connection:
            return repo.get_approval_decision(connection, proposal_id)

    def get_model_budget(self, run_id: str) -> ModelBudgetSnapshot:
        self.get_run(run_id)  # NOT_FOUND is explicit; no private file access by Main/UI.
        code = ErrorCode.PROVIDER_UNAVAILABLE
        if isinstance(self._model_client, ModelBudgetPort):
            try:
                result = ModelBudgetSnapshot.model_validate(self._model_client.get_model_budget(run_id))
                if result.run_id != run_id:
                    raise ValueError("Unrelated budget snapshot")
                return result
            except Exception:
                # Never expose private file paths or adapter/provider exception text.
                code = ErrorCode.BUDGET_EXCEEDED
        observed = repo.now_utc()
        return ModelBudgetSnapshot(
            run_id=run_id, status="UNAVAILABLE", utc_date=observed.date(), observed_at=observed,
            error=ServiceError(code=code, message="Authoritative budget unavailable; no model was called"),
        )

    def register_equipment_failure(self, incident: IncidentCreate) -> Incident:
        incident = IncidentCreate.model_validate(incident)
        with self._database.transaction() as connection:
            equipment = next((row for row in repo.list_equipment(connection) if row.id == incident.equipment_id), None)
            if equipment is None:
                raise CampusOpsError(ErrorCode.NOT_FOUND, "Equipment not found")
            if equipment.id in archived_ids(connection):
                raise CampusOpsError(ErrorCode.INVALID_STATE,"此器材已移除，请先在器材管理中恢复台账。")
            existing=sorted((row for row in repo.list_incidents(connection) if row.equipment_id==equipment.id and row.status=='OPEN'),key=lambda row:(row.effective_at,row.created_at,row.id))
            if existing:
                if equipment.status=='AVAILABLE':
                    raise CampusOpsError(ErrorCode.INVALID_STATE,"该器材存在未解决记录但当前为可用，状态需核对；未重复登记。")
                same=next((row for row in existing if row.effective_at==incident.effective_at and row.reason==incident.reason and row.kind==incident.kind),None)
                if same is not None:return same
                raise CampusOpsError(ErrorCode.INVALID_STATE,"此器材已有未解决故障（"+existing[0].id+"），请先处理原记录，不要重复登记。")
            created = Incident(**incident.model_dump(), id=repo.new_id("incident"), created_at=repo.now_utc())
            connection.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?,?)", (
                created.id, created.equipment_id, created.kind, repo.timestamp(created.effective_at),
                created.reason, created.status, repo.timestamp(created.created_at),
            ))
            connection.execute("UPDATE equipment SET status='FAULTED',version=version+1,updated_at_utc=? WHERE id=?",
                               (repo.timestamp(), created.equipment_id))
            from campusops.storage.reservation_events import invalidate_pending
            invalidate_pending(connection)
            repo.audit(connection, "EQUIPMENT_FAILURE_REGISTERED", "incident", created.id,
                       {"equipment": equipment.model_dump(mode="json")}, created.model_dump(mode="json"),
                       actor_type="administrator")
            return created

    def recover_equipment(self, request: EquipmentRecoveryCreate) -> EquipmentRecoveryResult:
        return EquipmentMaintenanceService(self._database).recover(request)

    def list_equipment_catalog(self,include_archived=False):
        return EquipmentCatalogService(self._database).list(include_archived)

    def create_equipment(self,request):
        return EquipmentCatalogService(self._database).mutate('create',request)

    def update_equipment(self,request):
        return EquipmentCatalogService(self._database).mutate('update',request)

    def archive_equipment(self,request):
        return EquipmentCatalogService(self._database).mutate('archive',request)

    def restore_equipment(self,request):
        return EquipmentCatalogService(self._database).mutate('restore',request)

    def find_equipment_command(self,idempotency_key):
        return EquipmentCatalogService(self._database).find(idempotency_key)

    def create_reservation(self, request):
        return self._approval.create_reservation(request)

    def cancel_reservation(self, request):
        return self._approval.cancel_reservation(request)

    def find_reservation_command(self, idempotency_key):
        return self._approval.find_reservation_command(idempotency_key)

    def plan_reservation_change(self, request):
        from campusops.services.reservation_changes import plan_change
        return plan_change(self, request)

    def get_reservation_change(self, change_id):
        from campusops.storage.reservation_events import get_change
        with self._database.connect(readonly=True) as connection:
            return get_change(connection, change_id)

    def list_reservation_changes(self, reservation_id=None):
        from campusops.storage.reservation_events import list_changes
        with self._database.connect(readonly=True) as connection:
            return list_changes(connection, reservation_id)

    def approve_reservation_change(self, change_id, actor_alias, idempotency_key):
        return self._approval.decide_reservation_change(change_id, actor_alias, idempotency_key)

    def reject_reservation_change(self, change_id, actor_alias, reason):
        identity = "reject_" + sha256(f"{change_id}:{actor_alias}:{reason}".encode("utf-8")).hexdigest()
        return self._approval.decide_reservation_change(change_id, actor_alias, identity, rejection=reason)

    def get_reservation_change_budget(self, change_id):
        self.get_reservation_change(change_id)
        observed = repo.now_utc()
        if isinstance(self._model_client, ModelBudgetPort):
            try:
                result = ModelBudgetSnapshot.model_validate(self._model_client.get_model_budget(change_id))
                if result.run_id == change_id:
                    return result
            except Exception:
                pass
        return ModelBudgetSnapshot(run_id=change_id, status="UNAVAILABLE", utc_date=observed.date(), observed_at=observed,
            error=ServiceError(code=ErrorCode.PROVIDER_UNAVAILABLE, message="权威预算暂不可用，未请求模型。"))

    @staticmethod
    def _failed(run_id: str, incident_id: str, code: ErrorCode, message: str, validation=None) -> PlanResult:
        return PlanResult(
            run_id=run_id, incident_id=incident_id, status="FAILED",
            validation=validation or ValidationResult(passed=False, issues=(message,)),
            explanation=message, error=ServiceError(code=code, message=message),
        )

    def start_workflow(self, incident_id: str) -> PlanResult:
        return self._start_workflow(incident_id)

    def start_workflow_once(self, incident_id: str, idempotency_key: str) -> PlanResult:
        if (not isinstance(idempotency_key, str) or not idempotency_key.strip()
                or len(idempotency_key) > 128):
            raise CampusOpsError(ErrorCode.INVALID_INPUT, "A stable workflow idempotency key is required")
        return self._start_workflow(incident_id, sha256(idempotency_key.encode("utf-8")).hexdigest())

    def _start_workflow(self, incident_id: str, claim_id: str | None = None) -> PlanResult:
        with self._database.transaction() as connection:
            if claim_id is not None:
                previous = connection.execute(
                    "SELECT after_json FROM audit_log WHERE action='WORKFLOW_START_CLAIMED' "
                    "AND entity_type='workflow_start' AND entity_id=? ORDER BY rowid LIMIT 1", (claim_id,),
                ).fetchone()
                if previous:
                    claim = json.loads(previous[0])
                    if claim["incident_id"] != incident_id:
                        raise CampusOpsError(ErrorCode.IDEMPOTENCY_CONFLICT, "Workflow key belongs to another incident")
                    saved = connection.execute("SELECT result_json FROM workflow_runs WHERE id=?", (claim["run_id"],)).fetchone()
                    if saved and saved[0]:
                        return PlanResult.model_validate_json(saved[0])
                    return self._failed(claim["run_id"], incident_id, ErrorCode.INVALID_STATE,
                                        "WORKFLOW_ALREADY_IN_PROGRESS: read persistent status; do not retry the model")
            rules = self._database.get_booking_rules()
            original = repo.snapshot(connection, rules=rules)
            agent_configs = repo.list_agent_configs(connection)
            incident = next((row for row in original.incidents if row.id == incident_id), None)
            if incident is None:
                raise CampusOpsError(ErrorCode.NOT_FOUND, "Incident not found")
            if incident.status != "OPEN":
                raise CampusOpsError(ErrorCode.INVALID_STATE, "Incident is no longer open")
            current_equipment=next((row for row in original.equipment if row.id==incident.equipment_id),None)
            if current_equipment is None or current_equipment.status=='AVAILABLE' or incident.equipment_id in archived_ids(connection):
                raise CampusOpsError(ErrorCode.INVALID_STATE,"此故障记录与器材当前状态不一致或器材已移除，请先核对；未启动模型。")
            run_id = repo.new_id("run")
            connection.execute(
                "INSERT INTO workflow_runs (id,incident_id,status,execution_mode,provider,model_id,snapshot_fingerprint,created_at_utc) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (run_id, incident_id, RunStatus.CREATED, self._execution_mode, self._provider,
                 self._model_id, original.fingerprint, repo.timestamp()),
            )
            repo.audit(connection, "RUN_CONTEXT_CAPTURED", "run", run_id, {}, {
                "resource_fingerprint": original.fingerprint,
                "rules": rules.model_dump(mode="json"),
                "agents": [row.model_dump(mode="json") for row in agent_configs],
                "model": {"provider": self._provider, "model_id": self._model_id,
                          "execution_mode": self._execution_mode},
            })
            if claim_id is not None:
                repo.audit(connection, "WORKFLOW_START_CLAIMED", "workflow_start", claim_id, {},
                           {"run_id": run_id, "incident_id": incident_id})
        tools = ReadonlyTools(self._database, run_id, incident_id, original,
                              rules=rules, agent_configs=agent_configs)
        impacted = impacted_reservations(original, incident)
        tools.append_run_step(RunStepCreate(
            run_id=run_id, node="main.impact_scan", decision_reason="Deterministic incident impact scan",
            output_digest=f"impacted_bookings={len(impacted)}", status=RunStatus.SCOPING,
        ))
        if not impacted:
            result = PlanResult(
                run_id=run_id, incident_id=incident_id, status="NO_IMPACT",
                validation=ValidationResult(passed=True, evidence_ids=(f"snapshot:{original.fingerprint}",)),
                explanation="No active reservations overlap the fault interval; no model was called",
            )
        elif self._orchestration is None:
            result = self._failed(run_id, incident_id, ErrorCode.NOT_IMPLEMENTED,
                                  "Agent Core orchestration has not been connected")
        elif self._execution_mode == ExecutionMode.REAL_API and self._model_client is None:
            result = self._failed(run_id, incident_id, ErrorCode.PROVIDER_UNAVAILABLE,
                                  "Real API execution requires an explicitly supplied model client")
        elif self._execution_mode in (ExecutionMode.SYSTEM, ExecutionMode.DEMO_FIXTURE):
            result = self._failed(run_id, incident_id, ErrorCode.INVALID_STATE,
                                  "An orchestrated run must explicitly identify real API or test execution")
        else:
            try:
                raw = self._orchestration.run_incident(incident_id, run_id, tools, self._model_client)
                result = PlanResult.model_validate(raw)
                if result.run_id != run_id or result.incident_id != incident_id or result.proposal_id is not None:
                    result = self._failed(run_id, incident_id, ErrorCode.INVALID_PROPOSAL,
                                          "Orchestration returned an unrelated run or a preassigned proposal ID")
                elif result.status == "NO_IMPACT":
                    result = self._failed(run_id, incident_id, ErrorCode.INVALID_PROPOSAL,
                                          "Orchestration cannot report NO_IMPACT when affected bookings exist")
            except CampusOpsError as exc:
                result = self._failed(run_id, incident_id, exc.error.code, exc.error.message)
            except Exception:
                # Provider exceptions can contain secrets. Persist only a safe generic error.
                result = self._failed(run_id, incident_id, ErrorCode.WORKFLOW_FAILED,
                                      "Orchestration raised an exception or returned invalid structured output")
        with self._database.transaction() as connection:
            run_status = RunStatus(result.status)
            if result.status == "WAIT_APPROVAL":
                try:
                    current = repo.snapshot(connection, rules=self._database.get_booking_rules())
                except CampusOpsError as exc:
                    result = self._failed(run_id, incident_id, exc.error.code, "Current rules are unavailable; no plan can be approved")
                    run_status = RunStatus.FAILED
            if result.status == "WAIT_APPROVAL":
                validation = validate_plan(current, incident_id, result.changes)
                if current.fingerprint != original.fingerprint:
                    result = self._failed(run_id, incident_id, ErrorCode.SNAPSHOT_CONFLICT,
                                          "Resources changed during planning; a fresh workflow is required")
                    run_status = RunStatus.STALE
                elif not validation.passed:
                    result = self._failed(run_id, incident_id, ErrorCode.INVALID_PROPOSAL,
                                          "Main deterministic validation rejected the proposed changes", validation)
                    run_status = RunStatus.FAILED
                else:
                    proposal_id = repo.new_id("proposal")
                    result = result.model_copy(update={
                        "proposal_id": proposal_id, "validation": validation,
                        "changes": tuple(sorted(result.changes, key=lambda item: item.reservation_id)),
                    })
                    connection.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?)", (
                        proposal_id, run_id, incident_id, "VALIDATED", current.fingerprint, 0,
                        result.explanation, validation.model_dump_json(), repo.timestamp(),
                    ))
                    for item in result.changes:
                        connection.execute("INSERT INTO proposal_items VALUES (?,?,?,?,?,?,?,?)", (
                            repo.new_id("item"), proposal_id, item.reservation_id, item.from_equipment_id,
                            item.to_equipment_id, repo.timestamp(item.starts_at), repo.timestamp(item.ends_at),
                            item.expected_reservation_version,
                        ))
            connection.execute(
                "UPDATE workflow_runs SET status=?,result_json=?,error_json=?,finished_at_utc=? WHERE id=?",
                (run_status, result.model_dump_json(), result.error.model_dump_json() if result.error else None,
                 None if run_status == RunStatus.WAIT_APPROVAL else repo.timestamp(), run_id),
            )
            repo.audit(connection, "WORKFLOW_RESULT", "run", run_id, {},
                       {"status": run_status, "execution_mode": self._execution_mode,
                        "error_code": result.error.code if result.error else None})
        return result

    def approve_proposal(self, proposal_id: str, actor_alias: str, idempotency_key: str):
        return self._approval.approve(proposal_id, actor_alias, idempotency_key)

    def reject_proposal(self, proposal_id: str, actor_alias: str, reason: str):
        return self._approval.reject(proposal_id, actor_alias, reason)

    def update_agent_config(self, agent_id: str, patch: AgentConfigPatch):
        if isinstance(patch, AgentConfigPatch):
            patch = patch.model_dump(exclude_none=True)
        patch = AgentRuntimeConfigPatch.model_validate(patch)
        with self._database.transaction() as connection:
            previous = next((row for row in repo.list_agent_configs(connection) if row.agent_id == agent_id), None)
            if previous is None:
                raise CampusOpsError(ErrorCode.NOT_FOUND, "Agent config not found")
            updated = previous.model_copy(update={
                **patch.model_dump(exclude_none=True), "updated_at": repo.now_utc(),
            })
            if updated.prompt_version not in SUPPORTED_PROMPT_VERSIONS:
                raise CampusOpsError(ErrorCode.INVALID_INPUT, "UNKNOWN_PROMPT_VERSION")
            if not set(updated.allowed_tools) <= ROLE_TOOL_LIMITS[updated.role]:
                raise CampusOpsError(ErrorCode.INVALID_INPUT, "TOOL_PERMISSION_OUTSIDE_ROLE_POLICY")
            connection.execute(
                "UPDATE agent_registry SET enabled=?,prompt_version=?,allowed_tools_json=?,updated_at_utc=? WHERE agent_id=?",
                (int(updated.enabled), updated.prompt_version, repo.canonical_json(updated.allowed_tools),
                 repo.timestamp(updated.updated_at), agent_id),
            )
            repo.audit(connection, "AGENT_CONFIG_UPDATED", "agent", agent_id,
                       previous.model_dump(mode="json"), updated.model_dump(mode="json"), actor_type="administrator")
            return updated

    def reset_demo_data(self, confirmation: str) -> None:
        if self._seed_asset is None:
            raise CampusOpsError(ErrorCode.NOT_IMPLEMENTED, "Demo reset has no configured seed asset")
        if confirmation != "RESET_FICTIONAL_DEMO":
            raise CampusOpsError(ErrorCode.INVALID_INPUT, "Explicit fictional-demo reset confirmation is required")
        seed_demo(self._database, self._seed_asset, confirmation=confirmation)
