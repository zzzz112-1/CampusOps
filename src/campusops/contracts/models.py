"""CampusOps contract 1.6.0. UTC, immutable models, no unknown fields."""

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(timezone.utc))]
Version = Annotated[int, Field(ge=1, strict=True)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True, revalidate_instances="always")


class RunStatus(StrEnum):
    CREATED = "CREATED"
    SCOPING = "SCOPING"
    PLANNING = "PLANNING"
    VERIFYING = "VERIFYING"
    WAIT_APPROVAL = "WAIT_APPROVAL"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    NO_IMPACT = "NO_IMPACT"
    REPLANNING = "REPLANNING"
    NEEDS_MANUAL = "NEEDS_MANUAL"
    REJECTED = "REJECTED"
    STALE = "STALE"
    FAILED = "FAILED"


class ExecutionMode(StrEnum):
    SYSTEM = "system"
    REAL_API = "real_api"
    OFFLINE_MOCK_TEST = "offline_mock_test"
    FAILURE_INJECTION_TEST = "failure_injection_test"
    DEMO_FIXTURE = "demo_fixture"


class ErrorCode(StrEnum):
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    RESERVATION_OVERLAP = "RESERVATION_OVERLAP"
    SNAPSHOT_CONFLICT = "SNAPSHOT_CONFLICT"
    INCOMPLETE_PLAN = "INCOMPLETE_PLAN"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    ALREADY_DECIDED = "ALREADY_DECIDED"
    DATABASE_BUSY = "DATABASE_BUSY"
    DATABASE_ERROR = "DATABASE_ERROR"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class EquipmentStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    FAULTED = "FAULTED"
    MAINTENANCE = "MAINTENANCE"


class ReservationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"


class ProposalStatus(StrEnum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class ApplyStatus(StrEnum):
    APPLIED = "APPLIED"
    STALE = "STALE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class ServiceError(ContractModel):
    code: ErrorCode
    message: str


class IncidentCreate(ContractModel):
    equipment_id: Identifier
    kind: Literal["equipment_failure"] = "equipment_failure"
    effective_at: UtcDatetime
    reason: ShortText


class Incident(IncidentCreate):
    id: Identifier
    status: Literal["OPEN", "RESOLVED"] = "OPEN"
    created_at: UtcDatetime


class Equipment(ContractModel):
    id: Identifier
    name: ShortText
    category: ShortText
    capabilities: tuple[ShortText, ...]
    status: EquipmentStatus
    version: Version
    updated_at: UtcDatetime


class EquipmentRecoveryCreate(ContractModel):
    equipment_id: Identifier
    expected_equipment_version: Version
    actor_alias: Annotated[str, Field(min_length=1, max_length=100)]
    note: ShortText
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def require_nonblank(self):
        if not all(value.strip() for value in (self.actor_alias, self.note, self.idempotency_key)):
            raise ValueError("维修操作人、说明和请求标识不能为空")
        return self


class EquipmentRecoveryResult(ContractModel):
    equipment: Equipment
    resolved_incident_ids: tuple[Identifier, ...] = ()
    invalidated_proposal_ids: tuple[Identifier, ...] = ()
    recovered_at: UtcDatetime


class TimeWindow(ContractModel):
    starts_at: UtcDatetime
    ends_at: UtcDatetime

    @model_validator(mode="after")
    def validate_window(self):
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be earlier than ends_at")
        return self


class Reservation(TimeWindow):
    id: Identifier
    equipment_id: Identifier
    borrower_alias: ShortText
    required_capabilities: tuple[ShortText, ...]
    status: ReservationStatus
    version: Version
    updated_at: UtcDatetime


class ProposalItem(TimeWindow):
    reservation_id: Identifier
    from_equipment_id: Identifier
    to_equipment_id: Identifier
    expected_reservation_version: Version


class ValidationIssue(ContractModel):
    code: ErrorCode
    message: str
    entity_id: str | None = None


class ValidationResult(ContractModel):
    passed: bool
    issues: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    violations: tuple[ValidationIssue, ...] = ()

    @model_validator(mode="after")
    def validate_consistency(self):
        if self.passed and (self.issues or self.violations):
            raise ValueError("passed validation cannot contain issues")
        if not self.passed and not (self.issues or self.violations):
            raise ValueError("failed validation requires a reason")
        return self


class PlanResult(ContractModel):
    run_id: Identifier
    incident_id: Identifier
    proposal_id: Identifier | None = None
    status: Literal["WAIT_APPROVAL", "NO_IMPACT", "NEEDS_MANUAL", "FAILED"]
    changes: tuple[ProposalItem, ...] = ()
    validation: ValidationResult
    explanation: str
    error: ServiceError | None = None

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.status == "WAIT_APPROVAL":
            if not self.changes or not self.validation.passed or self.error:
                raise ValueError("WAIT_APPROVAL requires valid nonempty changes and no error")
        elif self.changes or self.proposal_id:
            raise ValueError("only WAIT_APPROVAL can contain proposal changes")
        return self


class ApplyResult(ContractModel):
    proposal_id: Identifier
    status: ApplyStatus
    changed_reservation_ids: tuple[Identifier, ...] = ()
    message: str
    error: ServiceError | None = None

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.status != ApplyStatus.APPLIED and self.changed_reservation_ids:
            raise ValueError("unsuccessful apply cannot report changed bookings")
        return self


class AgentConfig(ContractModel):
    agent_id: Identifier
    display_name: ShortText
    role: Literal["impact", "planner", "verifier"]
    skills: tuple[ShortText, ...]
    allowed_tools: tuple[ShortText, ...]
    enabled: bool
    prompt_version: ShortText
    updated_at: UtcDatetime


class AgentConfigPatch(ContractModel):
    enabled: bool | None = None
    prompt_version: ShortText | None = None


class BookingRules(ContractModel):
    storage_timezone: Literal["UTC"] = "UTC"
    display_timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    interval_semantics: Literal["[start,end)"] = "[start,end)"
    preserve_original_times: Literal[True] = True
    max_replans: Literal[2] = 2


class AgentRuntimeConfigPatch(AgentConfigPatch):
    """Additive patch; the original 1.0 AgentConfigPatch remains unchanged."""

    allowed_tools: Annotated[tuple[ShortText, ...], Field(max_length=6)] | None = None

    @model_validator(mode="after")
    def unique_tools(self):
        if self.allowed_tools is not None and len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("tool permissions must be unique")
        return self


class BookingRuleDocument(ContractModel):
    rule_id: Identifier
    version: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    content: Annotated[str, Field(min_length=1, max_length=2000)]
    source: ShortText
    fictional: Literal[True] = True
    incident_kind: Literal["equipment_failure"] = "equipment_failure"


class BookingRuleCatalog(BookingRules):
    """Runtime subtype carrying the actually matched fictional local rules."""

    ruleset_id: Identifier
    version: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
    source: ShortText
    fictional: Literal[True] = True
    rules: Annotated[tuple[BookingRuleDocument, ...], Field(min_length=1, max_length=32)]
    fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def unique_rules(self):
        ids = [row.rule_id for row in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule IDs must be unique")
        return self


class ResourceSnapshot(ContractModel):
    fingerprint: str
    equipment: tuple[Equipment, ...]
    reservations: tuple[Reservation, ...]
    incidents: tuple[Incident, ...]


class RunStepCreate(ContractModel):
    run_id: Identifier
    node: ShortText
    agent_id: Identifier | None = None
    tool_name: ShortText | None = None
    decision_reason: ShortText
    input_digest: Annotated[str, Field(max_length=500)] = ""
    output_digest: Annotated[str, Field(max_length=500)] = ""
    duration_ms: Annotated[int, Field(ge=0, strict=True)] = 0
    error: ServiceError | None = None
    model_calls: Annotated[int, Field(ge=0, le=1, strict=True)] = 0
    retry_count: Annotated[int, Field(ge=0, le=2, strict=True)] = 0
    status: RunStatus | None = None


class RunStep(RunStepCreate):
    id: Identifier
    seq: Annotated[int, Field(ge=1)]
    execution_mode: ExecutionMode
    created_at: UtcDatetime


class WorkflowRun(ContractModel):
    id: Identifier
    incident_id: Identifier
    status: RunStatus
    execution_mode: ExecutionMode
    provider: str | None = None
    model_id: str | None = None
    model_calls: Annotated[int, Field(ge=0)]
    retry_count: Annotated[int, Field(ge=0, le=2)]
    snapshot_fingerprint: str
    created_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    result: PlanResult | None = None
    error: ServiceError | None = None


class Proposal(ContractModel):
    id: Identifier
    run_id: Identifier
    incident_id: Identifier
    status: ProposalStatus
    snapshot_fingerprint: str
    changes: tuple[ProposalItem, ...]
    validation: ValidationResult
    explanation: str
    created_at: UtcDatetime


class ModelMessage(ContractModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ModelRequest(ContractModel):
    run_id: Identifier
    agent_id: Identifier
    messages: tuple[ModelMessage, ...]
    response_schema: str


class ModelResponse(ContractModel):
    content: str
    provider: ShortText
    model_id: ShortText
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None


class ModelUsage(ContractModel):
    utc_date: str
    daily_model_calls: Annotated[int, Field(ge=0)]


class ApprovalDecision(ContractModel):
    proposal_id: Identifier
    decision: Literal["APPROVE", "REJECT"]
    actor_alias: ShortText
    reason: ShortText | None = None
    created_at: UtcDatetime


class ModelBudgetSnapshot(ContractModel):
    """Conservative pre-attempt reservations, not tokens, money or account quota."""

    run_id: Identifier
    scope: Literal["current_worktree"] = "current_worktree"
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    utc_date: date
    observed_at: UtcDatetime
    per_run_limit: Annotated[int, Field(ge=1, strict=True)] | None = None
    daily_limit: Annotated[int, Field(ge=1, strict=True)] | None = None
    run_reserved: Annotated[int, Field(ge=0, strict=True)] | None = None
    daily_reserved: Annotated[int, Field(ge=0, strict=True)] | None = None
    run_remaining: Annotated[int, Field(ge=0, strict=True)] | None = None
    daily_remaining: Annotated[int, Field(ge=0, strict=True)] | None = None
    error: ServiceError | None = None

    @model_validator(mode="after")
    def validate_counters(self):
        fields = (self.per_run_limit, self.daily_limit, self.run_reserved,
                  self.daily_reserved, self.run_remaining, self.daily_remaining)
        if self.observed_at.date() != self.utc_date:
            raise ValueError("budget date must match the UTC observation date")
        if self.status == "AVAILABLE":
            if any(value is None for value in fields) or self.error is not None:
                raise ValueError("available budget requires all counters and no error")
            if (self.run_remaining != max(0, self.per_run_limit - self.run_reserved)
                    or self.daily_remaining != max(0, self.daily_limit - self.daily_reserved)):
                raise ValueError("budget remaining must match reserved counters and limits")
        elif any(value is not None for value in fields) or self.error is None:
            raise ValueError("unavailable budget requires unknown counters and a safe error")
        return self


class ReservationDraft(TimeWindow):
    equipment_id: Identifier
    borrower_alias: Annotated[str, Field(min_length=1, max_length=100)]
    required_capabilities: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def check_booking_fields(self):
        if not self.borrower_alias.strip() or len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("匿名借用者不能为空，能力要求不能重复")
        return self


class ReservationCreate(ReservationDraft):
    expected_equipment_version: Version
    actor_alias: Annotated[str, Field(min_length=1, max_length=100)]
    note: Annotated[str, Field(max_length=500)] = ""
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def check_actor(self):
        if not self.actor_alias.strip() or not self.idempotency_key.strip():
            raise ValueError("确认人和请求标识不能为空")
        return self


class ReservationCancelCreate(ContractModel):
    reservation_id: Identifier
    expected_reservation_version: Version
    actor_alias: Annotated[str, Field(min_length=1, max_length=100)]
    reason: ShortText
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def check_nonblank(self):
        if not all(value.strip() for value in (self.actor_alias, self.reason, self.idempotency_key)):
            raise ValueError("取消原因、确认人和请求标识不能为空")
        return self


class ReservationMutationResult(ContractModel):
    reservation: Reservation
    invalidated_proposal_ids: tuple[Identifier, ...] = ()
    message: str


class ReservationChangeCreate(TimeWindow):
    reservation_id: Identifier
    expected_reservation_version: Version
    requested_equipment_id: Identifier | None = None
    borrower_alias: Annotated[str, Field(min_length=1, max_length=100)]
    required_capabilities: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=20)]
    actor_alias: Annotated[str, Field(min_length=1, max_length=100)]
    reason: ShortText
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def check_fields(self):
        if not all(value.strip() for value in (self.borrower_alias, self.actor_alias, self.reason, self.idempotency_key)):
            raise ValueError("修改所需字段不能为空")
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("能力要求不能重复")
        return self


class ReservationChangePolicy(ContractModel):
    policy_id: Literal["single-reservation-change"] = "single-reservation-change"
    version: Literal["1"] = "1"
    source: str = "所有者明确授权的单预约修改流程；虚构校园器材演示，不代表学校官方规则"
    max_replans: Literal[2] = 2
    rules: tuple[str, ...] = (
        "仅修改请求指定的一条预约，保留原预约编号，其他预约不变。",
        "时间只可使用用户明确请求的起止时间；不能由模型自行改期。",
        "器材必须可用、未被开放故障封禁、包含全部所需能力，并且没有有效预约时间冲突。",
        "模型只能从程序给出的候选中选择；必须独立验证，人工批准后事务再验证才能写入。",
    )


class ReservationChangeContext(ContractModel):
    id: Identifier
    kind: Literal["reservation_change"] = "reservation_change"
    before: Reservation
    request: ReservationChangeCreate
    snapshot_fingerprint: str


class ReservationChangePlanResult(ContractModel):
    change_id: Identifier
    status: Literal["WAIT_APPROVAL", "NEEDS_MANUAL", "FAILED"]
    after: Reservation | None = None
    validation: ValidationResult
    explanation: str
    error: ServiceError | None = None

    @model_validator(mode="after")
    def valid_plan(self):
        if self.status == "WAIT_APPROVAL":
            if self.after is None or not self.validation.passed or self.error:
                raise ValueError("待审批修改必须有合法方案")
        elif self.after is not None:
            raise ValueError("未成功规划不能包含可执行修改")
        return self


class ReservationChangeRun(ContractModel):
    id: Identifier
    context: ReservationChangeContext
    status: RunStatus
    execution_mode: ExecutionMode
    provider: str | None = None
    model_id: str | None = None
    model_calls: Annotated[int, Field(ge=0)] = 0
    retry_count: Annotated[int, Field(ge=0, le=2)] = 0
    steps: tuple[RunStep, ...] = ()
    created_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    result: ReservationChangePlanResult | None = None
    error: ServiceError | None = None
    decision: "ReservationChangeDecision | None" = None


class ReservationChangeDecision(ContractModel):
    change_id: Identifier
    status: ApplyStatus
    reservation: Reservation | None = None
    actor_alias: str
    reason: str
    decided_at: UtcDatetime
    message: str
    error: ServiceError | None = None


ReservationChangeRun.model_rebuild()


class EquipmentCreate(ContractModel):
    name: ShortText
    category: ShortText
    capabilities: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=20)]
    actor_alias: Annotated[str, Field(min_length=1, max_length=100)]
    note: Annotated[str, Field(max_length=500)] = ""
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def meaningful_fields(self):
        if not all(value.strip() for value in (self.name,self.category,self.actor_alias,self.idempotency_key,*self.capabilities)):
            raise ValueError("器材名称、类别、能力、确认人和请求标识不能为空")
        if len(set(self.capabilities))!=len(self.capabilities):
            raise ValueError("能力标签不能重复")
        return self


class EquipmentUpdate(EquipmentCreate):
    equipment_id: Identifier
    expected_equipment_version: Version


class EquipmentArchiveCreate(ContractModel):
    equipment_id: Identifier
    expected_equipment_version: Version
    actor_alias: Annotated[str, Field(min_length=1, max_length=100)]
    reason: ShortText
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def meaningful_fields(self):
        if not all(value.strip() for value in (self.actor_alias,self.reason,self.idempotency_key)):
            raise ValueError("操作人、原因和请求标识不能为空")
        return self


class EquipmentRestoreCreate(EquipmentArchiveCreate):
    pass


class EquipmentCatalogEntry(ContractModel):
    equipment: Equipment
    archived: bool = False
    archived_at: UtcDatetime | None = None
    archive_previous_status: EquipmentStatus | None = None


class EquipmentCatalogMutationResult(ContractModel):
    record: EquipmentCatalogEntry
    invalidated_proposal_ids: tuple[Identifier, ...] = ()
    message: str


PUBLIC_MODELS = (
    ServiceError, IncidentCreate, Incident, Equipment, TimeWindow, Reservation,
    ProposalItem, ValidationIssue, ValidationResult, PlanResult, ApplyResult,
    AgentConfig, AgentConfigPatch, BookingRules, ResourceSnapshot, RunStepCreate,
    RunStep, WorkflowRun, Proposal, ModelMessage, ModelRequest, ModelResponse, ModelUsage,
    ApprovalDecision, ModelBudgetSnapshot,
    AgentRuntimeConfigPatch, BookingRuleDocument, BookingRuleCatalog,
    EquipmentRecoveryCreate, EquipmentRecoveryResult,
    ReservationDraft, ReservationCreate, ReservationCancelCreate, ReservationMutationResult,
    ReservationChangeCreate, ReservationChangePolicy, ReservationChangeContext,
    ReservationChangePlanResult, ReservationChangeRun, ReservationChangeDecision,
    EquipmentCreate, EquipmentUpdate, EquipmentArchiveCreate, EquipmentRestoreCreate,
    EquipmentCatalogEntry, EquipmentCatalogMutationResult,
)
