"""Typed mapping helpers. Only Main services receive SQLite connections."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import (
    AgentConfig, ApprovalDecision, Equipment, ErrorCode, Incident, PlanResult, Proposal, ProposalItem,
    Reservation, ResourceSnapshot, RunStep, ServiceError, ValidationResult, WorkflowRun,
)
from campusops.domain.rules import load_booking_rules


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or now_utc()).astimezone(timezone.utc).isoformat(timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def list_equipment(connection) -> tuple[Equipment, ...]:
    return tuple(Equipment(
        id=row["id"], name=row["name"], category=row["category"],
        capabilities=json.loads(row["capabilities_json"]), status=row["status"],
        version=row["version"], updated_at=row["updated_at_utc"],
    ) for row in connection.execute("SELECT * FROM equipment ORDER BY id"))


def list_reservations(connection) -> tuple[Reservation, ...]:
    return tuple(Reservation(
        id=row["id"], equipment_id=row["equipment_id"], borrower_alias=row["borrower_alias"],
        starts_at=row["starts_at_utc"], ends_at=row["ends_at_utc"],
        required_capabilities=json.loads(row["requirements_json"]), status=row["status"],
        version=row["version"], updated_at=row["updated_at_utc"],
    ) for row in connection.execute("SELECT * FROM reservations ORDER BY id"))


def list_incidents(connection) -> tuple[Incident, ...]:
    return tuple(Incident(
        id=row["id"], equipment_id=row["equipment_id"], kind=row["kind"],
        effective_at=row["effective_at_utc"], reason=row["reason"], status=row["status"],
        created_at=row["created_at_utc"],
    ) for row in connection.execute("SELECT * FROM incidents ORDER BY id"))


def snapshot(connection, *, rules=None) -> ResourceSnapshot:
    # Multiple SELECTs must share one read/write transaction to avoid torn snapshots.
    equipment = list_equipment(connection)
    reservations = list_reservations(connection)
    incidents = list_incidents(connection)
    payload = {
        "equipment": [row.model_dump(mode="json") for row in equipment],
        "reservations": [row.model_dump(mode="json") for row in reservations],
        "incidents": [row.model_dump(mode="json") for row in incidents],
        "booking_rules": (rules if rules is not None else load_booking_rules()).model_dump(mode="json"),
    }
    return ResourceSnapshot(
        fingerprint=sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
        equipment=equipment, reservations=reservations, incidents=incidents,
    )


def list_agent_configs(connection) -> tuple[AgentConfig, ...]:
    return tuple(AgentConfig(
        agent_id=row["agent_id"], display_name=row["display_name"], role=row["role"],
        skills=json.loads(row["skills_json"]), allowed_tools=json.loads(row["allowed_tools_json"]),
        enabled=bool(row["enabled"]), prompt_version=row["prompt_version"], updated_at=row["updated_at_utc"],
    ) for row in connection.execute("SELECT * FROM agent_registry ORDER BY agent_id"))


def get_proposal(connection, proposal_id: str) -> Proposal:
    row = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise CampusOpsError(ErrorCode.NOT_FOUND, "Proposal not found")
    changes = tuple(ProposalItem(
        reservation_id=item["reservation_id"], from_equipment_id=item["old_equipment_id"],
        to_equipment_id=item["new_equipment_id"], starts_at=item["starts_at_utc"],
        ends_at=item["ends_at_utc"], expected_reservation_version=item["reservation_version"],
    ) for item in connection.execute(
        "SELECT * FROM proposal_items WHERE proposal_id=? ORDER BY reservation_id", (proposal_id,),
    ))
    return Proposal(
        id=row["id"], run_id=row["run_id"], incident_id=row["incident_id"],
        status=row["status"], snapshot_fingerprint=row["snapshot_fingerprint"], changes=changes,
        validation=ValidationResult.model_validate_json(row["validation_json"]),
        explanation=row["reasoning"], created_at=row["created_at_utc"],
    )


def get_run(connection, run_id: str) -> WorkflowRun:
    row = connection.execute("SELECT * FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise CampusOpsError(ErrorCode.NOT_FOUND, "Workflow run not found")
    return WorkflowRun(
        id=row["id"], incident_id=row["incident_id"], status=row["status"],
        execution_mode=row["execution_mode"], provider=row["provider"], model_id=row["model_id"],
        model_calls=row["model_calls"], retry_count=row["retry_count"],
        snapshot_fingerprint=row["snapshot_fingerprint"], created_at=row["created_at_utc"],
        finished_at=row["finished_at_utc"],
        result=PlanResult.model_validate_json(row["result_json"]) if row["result_json"] else None,
        error=ServiceError.model_validate_json(row["error_json"]) if row["error_json"] else None,
    )


def get_approval_decision(connection, proposal_id: str) -> ApprovalDecision | None:
    # A missing proposal differs from an existing, undecided proposal.
    get_proposal(connection, proposal_id)
    row = connection.execute(
        "SELECT proposal_id,decision,actor_alias,reason,created_at_utc FROM approvals WHERE proposal_id=?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        return None
    return ApprovalDecision(
        proposal_id=row["proposal_id"], decision=row["decision"], actor_alias=row["actor_alias"],
        reason=row["reason"] or None, created_at=row["created_at_utc"],
    )


def list_run_steps(connection, run_id: str) -> tuple[RunStep, ...]:
    return tuple(RunStep(
        id=row["id"], run_id=row["run_id"], seq=row["seq"], node=row["node"],
        agent_id=row["agent_id"], tool_name=row["tool_name"], decision_reason=row["decision_reason"],
        input_digest=row["input_digest"], output_digest=row["output_digest"], duration_ms=row["duration_ms"],
        error=ServiceError.model_validate_json(row["error"]) if row["error"] else None,
        model_calls=row["model_calls"], retry_count=row["retry_count"], status=row["status"],
        execution_mode=row["execution_mode"], created_at=row["created_at_utc"],
    ) for row in connection.execute("SELECT * FROM run_steps WHERE run_id=? ORDER BY seq", (run_id,)))


def audit(connection, action: str, entity_type: str, entity_id: str, before, after, *, actor_type="system"):
    connection.execute("INSERT INTO audit_log VALUES (?,?,?,?,?,?,?,?)", (
        new_id("audit"), actor_type, action, entity_type, entity_id,
        canonical_json(before), canonical_json(after), timestamp(),
    ))
