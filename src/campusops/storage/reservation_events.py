"""Typed reservation-change records in the existing append-only Main audit table."""
from hashlib import sha256
from datetime import datetime
import json

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import (
    ErrorCode, ReservationChangeRun, ReservationChangePlanResult, ReservationChangePolicy,
    ResourceSnapshot, RunStep, RunStatus, ReservationChangeDecision,
)
from campusops.storage import repository as repo
from campusops.domain.reservations import CHANGE_CANDIDATE_POLICY


def key_digest(key: str) -> str:
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise CampusOpsError(ErrorCode.INVALID_INPUT, "请求标识无效。")
    return sha256(key.encode("utf-8")).hexdigest()


def change_snapshot(connection, database) -> ResourceSnapshot:
    snapshot = repo.snapshot(connection, rules=database.get_booking_rules())
    fingerprint = sha256(repo.canonical_json({"resources": snapshot.fingerprint,
        "change_policy": ReservationChangePolicy().model_dump(mode="json"),
        "candidate_policy": CHANGE_CANDIDATE_POLICY}).encode("utf-8")).hexdigest()
    return snapshot.model_copy(update={"fingerprint": fingerprint})


def change_event(connection, change_id: str, action: str, payload, *, actor_type="system"):
    repo.audit(connection, action, "reservation_change", change_id, {}, payload, actor_type=actor_type)


def get_change(connection, change_id: str) -> ReservationChangeRun:
    rows = connection.execute("SELECT action,after_json FROM audit_log WHERE entity_type='reservation_change' "
                              "AND entity_id=? ORDER BY rowid", (change_id,)).fetchall()
    if not rows or rows[0]["action"] != "RESERVATION_CHANGE_CAPTURED":
        raise CampusOpsError(ErrorCode.NOT_FOUND, "未找到预约修改记录。")
    run = ReservationChangeRun.model_validate(json.loads(rows[0]["after_json"])["run"])
    for row in rows[1:]:
        value = json.loads(row["after_json"])
        if row["action"] == "RESERVATION_CHANGE_STEP":
            step = RunStep.model_validate(value["step"])
            run = run.model_copy(update={"steps": run.steps + (step,), "model_calls": run.model_calls + step.model_calls,
                "retry_count": max(run.retry_count, step.retry_count), "status": step.status or run.status})
        elif row["action"] == "RESERVATION_CHANGE_RESULT":
            result = ReservationChangePlanResult.model_validate(value["result"])
            run = run.model_copy(update={"result": result, "status": RunStatus(value["status"]),
                "error": result.error, "finished_at": None if value["status"] == "WAIT_APPROVAL" else datetime.fromisoformat(value["at"])})
        elif row["action"] == "RESERVATION_CHANGE_DECIDED":
            decision = ReservationChangeDecision.model_validate(value["decision"])
            run = run.model_copy(update={"status": RunStatus(decision.status), "decision": decision,
                "finished_at": decision.decided_at})
        elif row["action"] == "RESERVATION_CHANGE_STALE":
            run = run.model_copy(update={"status": RunStatus.STALE, "finished_at": datetime.fromisoformat(value["at"])})
    # Revalidate at the storage boundary; historical strings become aware UTC.
    return ReservationChangeRun.model_validate(run.model_dump())


def list_changes(connection, reservation_id: str | None = None):
    ids = [row[0] for row in connection.execute("SELECT entity_id FROM audit_log WHERE action='RESERVATION_CHANGE_CAPTURED' ORDER BY rowid DESC")]
    runs = tuple(get_change(connection, identity) for identity in ids)
    return tuple(run for run in runs if reservation_id is None or run.context.before.id == reservation_id)


def invalidate_pending(connection, *, exclude_change: str | None = None) -> tuple[str, ...]:
    pending = connection.execute("SELECT id,run_id FROM proposals WHERE status IN ('PROPOSED','VALIDATED') ORDER BY id").fetchall()
    for row in pending:
        connection.execute("UPDATE proposals SET status='STALE' WHERE id=?", (row["id"],))
        connection.execute("UPDATE workflow_runs SET status='STALE',finished_at_utc=? WHERE id=? AND status='WAIT_APPROVAL'",
                           (repo.timestamp(), row["run_id"]))
    changes = []
    for run in list_changes(connection):
        if run.id != exclude_change and run.status == "WAIT_APPROVAL":
            change_event(connection, run.id, "RESERVATION_CHANGE_STALE", {"at": repo.timestamp()})
            changes.append(run.id)
    return tuple(row["id"] for row in pending) + tuple(changes)


def extra_model_calls(connection, utc_date: str) -> int:
    count = 0
    for row in connection.execute("SELECT after_json FROM audit_log WHERE action='RESERVATION_CHANGE_STEP' AND substr(created_at_utc,1,10)=?", (utc_date,)):
        step = json.loads(row[0])["step"]
        if step["execution_mode"] == "real_api":
            count += step["model_calls"]
    return count
