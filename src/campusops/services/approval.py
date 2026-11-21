"""The sole runtime reservation writer. No LLM or UI callback in transactions."""

import logging
import sqlite3

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import ApplyResult, ApplyStatus, ErrorCode, ProposalStatus, ServiceError
from campusops.domain.validation import validate_plan
from campusops.storage.database import Database
from campusops.storage import repository as repo
from campusops.storage import reservation_events as events
from campusops.domain.reservations import validate_booking, validate_change
from campusops.contracts.models import (
    Reservation, ReservationCreate, ReservationCancelCreate, ReservationMutationResult, ReservationChangeDecision,
)
from hashlib import sha256
import json


def failure(proposal_id: str, code: ErrorCode, message: str, *, status=ApplyStatus.FAILED) -> ApplyResult:
    return ApplyResult(proposal_id=proposal_id, status=status, message=message,
                       error=ServiceError(code=code, message=message))


class ApprovalService:
    def __init__(self, database: Database):
        self._database = database

    def find_reservation_command(self, idempotency_key: str) -> ReservationMutationResult | None:
        identity = events.key_digest(idempotency_key)
        with self._database.connect(readonly=True) as connection:
            row = connection.execute("SELECT after_json FROM audit_log WHERE entity_type='reservation_command' AND entity_id=?", (identity,)).fetchone()
            return ReservationMutationResult.model_validate(json.loads(row[0])["result"]) if row else None

    @staticmethod
    def _saved_command(connection, request, operation):
        identity = events.key_digest(request.idempotency_key)
        request_hash = sha256(request.model_dump_json().encode("utf-8")).hexdigest()
        row = connection.execute("SELECT after_json FROM audit_log WHERE entity_type='reservation_command' AND entity_id=?", (identity,)).fetchone()
        if row:
            value = json.loads(row[0])
            if value["request_hash"] != request_hash or value["operation"] != operation:
                raise CampusOpsError(ErrorCode.IDEMPOTENCY_CONFLICT, "此请求标识已用于其他预约操作，请核对保存结果。")
            return identity, request_hash, ReservationMutationResult.model_validate(value["result"])
        return identity, request_hash, None

    @staticmethod
    def _finish_command(connection, request, operation, identity, request_hash, booking, before):
        invalidated = events.invalidate_pending(connection)
        result = ReservationMutationResult(reservation=booking, invalidated_proposal_ids=invalidated,
            message="预约已创建并保存。" if operation == "create" else "预约已取消，历史记录保留。")
        repo.audit(connection, "RESERVATION_CREATED" if operation == "create" else "RESERVATION_CANCELLED",
            "reservation_command", identity, before,
            {"operation": operation, "request_hash": request_hash, "actor_alias": request.actor_alias,
             "note": getattr(request, "note", ""), "reason": getattr(request, "reason", ""),
             "result": result.model_dump(mode="json")}, actor_type="administrator")
        return result

    def create_reservation(self, request: ReservationCreate) -> ReservationMutationResult:
        request = ReservationCreate.model_validate(request)
        try:
            with self._database.transaction() as connection:
                identity, request_hash, saved = self._saved_command(connection, request, "create")
                if saved is not None:
                    return saved
                snapshot = repo.snapshot(connection, rules=self._database.get_booking_rules())
                equipment = next((row for row in snapshot.equipment if row.id == request.equipment_id), None)
                if equipment is None:
                    raise CampusOpsError(ErrorCode.NOT_FOUND, "未找到所选器材。")
                if equipment.version != request.expected_equipment_version:
                    raise CampusOpsError(ErrorCode.SNAPSHOT_CONFLICT, "器材状态已变化，请重新预览并确认。")
                booking = Reservation(id=repo.new_id("booking"), equipment_id=request.equipment_id,
                    borrower_alias=request.borrower_alias.strip(), starts_at=request.starts_at, ends_at=request.ends_at,
                    required_capabilities=request.required_capabilities, status="ACTIVE", version=1, updated_at=repo.now_utc())
                validation = validate_booking(snapshot, booking)
                if not validation.passed:
                    raise CampusOpsError(validation.violations[0].code, validation.issues[0])
                connection.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?,?)", (
                    booking.id, booking.equipment_id, booking.borrower_alias, repo.timestamp(booking.starts_at), repo.timestamp(booking.ends_at),
                    json.dumps(booking.required_capabilities), booking.status, booking.version, repo.timestamp(booking.updated_at),
                ))
                observed = next(row for row in repo.list_reservations(connection) if row.id == booking.id)
                if observed != booking:
                    raise CampusOpsError(ErrorCode.DATABASE_ERROR, "预约写入后核对失败，事务已回滚。")
                return self._finish_command(connection, request, "create", identity, request_hash, observed, {})
        except sqlite3.Error:
            raise CampusOpsError(ErrorCode.DATABASE_ERROR, "新增预约事务未完成，所有变更已回滚。") from None

    def cancel_reservation(self, request: ReservationCancelCreate) -> ReservationMutationResult:
        request = ReservationCancelCreate.model_validate(request)
        try:
            with self._database.transaction() as connection:
                identity, request_hash, saved = self._saved_command(connection, request, "cancel")
                if saved is not None:
                    return saved
                current = next((row for row in repo.list_reservations(connection) if row.id == request.reservation_id), None)
                if current is None:
                    raise CampusOpsError(ErrorCode.NOT_FOUND, "未找到要取消的预约。")
                if current.status != "ACTIVE" or current.version != request.expected_reservation_version:
                    raise CampusOpsError(ErrorCode.SNAPSHOT_CONFLICT, "预约已变化或已取消，请刷新核对。")
                cursor = connection.execute("UPDATE reservations SET status='CANCELLED',version=version+1,updated_at_utc=? WHERE id=? AND version=?",
                                            (repo.timestamp(), current.id, current.version))
                observed = next(row for row in repo.list_reservations(connection) if row.id == current.id)
                if cursor.rowcount != 1 or observed != current.model_copy(update={"status": "CANCELLED", "version": current.version + 1, "updated_at": observed.updated_at}):
                    raise CampusOpsError(ErrorCode.DATABASE_ERROR, "取消后的预约读回不一致，事务已回滚。")
                return self._finish_command(connection, request, "cancel", identity, request_hash, observed, current.model_dump(mode="json"))
        except sqlite3.Error:
            raise CampusOpsError(ErrorCode.DATABASE_ERROR, "取消预约事务未完成，所有变更已回滚。") from None

    def decide_reservation_change(self, change_id, actor_alias, idempotency_key, *, rejection=None):
        actor = actor_alias.strip() if isinstance(actor_alias, str) else ""
        if not 1 <= len(actor) <= 100 or rejection is not None and not 1 <= len(rejection.strip()) <= 500:
            raise CampusOpsError(ErrorCode.INVALID_INPUT, "请填写确认人匿名别名和有效的拒绝原因。")
        identity = events.key_digest(idempotency_key)
        try:
            with self._database.transaction() as connection:
                for row in connection.execute("SELECT entity_id,after_json FROM audit_log WHERE action='RESERVATION_CHANGE_DECIDED'"):
                    value = json.loads(row["after_json"])
                    if value["key"] == identity and (row["entity_id"] != change_id or value["decision"]["actor_alias"] != actor
                                                     or value["operation"] != ("reject" if rejection is not None else "approve")):
                        raise CampusOpsError(ErrorCode.IDEMPOTENCY_CONFLICT, "此审批请求标识已用于其他操作。")
                run = events.get_change(connection, change_id)
                if run.decision is not None:
                    if rejection is not None and run.decision.status == "APPLIED":
                        raise CampusOpsError(ErrorCode.ALREADY_DECIDED, "此修改已经批准执行，不能再拒绝。")
                    return run.decision
                if run.result is None or run.result.status != "WAIT_APPROVAL" or run.result.after is None:
                    raise CampusOpsError(ErrorCode.INVALID_STATE, "当前没有可以审批的修改方案。")
                if run.status not in ("WAIT_APPROVAL", "STALE"):
                    raise CampusOpsError(ErrorCode.INVALID_STATE, "当前修改请求不能审批。")
                current_snapshot = events.change_snapshot(connection, self._database)
                validation = validate_change(current_snapshot, run.context, run.result.after)
                stale = run.status == "STALE" or current_snapshot.fingerprint != run.context.snapshot_fingerprint or not validation.passed
                status = "REJECTED" if rejection is not None else "STALE" if stale else "APPLIED"
                updated = None
                if status == "APPLIED":
                    draft = run.result.after
                    cursor = connection.execute("UPDATE reservations SET equipment_id=?,borrower_alias=?,starts_at_utc=?,ends_at_utc=?,"
                        "requirements_json=?,version=version+1,updated_at_utc=? WHERE id=? AND version=? AND status='ACTIVE'", (
                        draft.equipment_id, draft.borrower_alias, repo.timestamp(draft.starts_at), repo.timestamp(draft.ends_at),
                        json.dumps(draft.required_capabilities), repo.timestamp(), draft.id, run.context.before.version))
                    if cursor.rowcount != 1:
                        raise CampusOpsError(ErrorCode.SNAPSHOT_CONFLICT, "原预约在提交时已变化。")
                    updated = next(row for row in repo.list_reservations(connection) if row.id == draft.id)
                    expected = draft.model_copy(update={"version": draft.version + 1, "updated_at": updated.updated_at})
                    if updated != expected:
                        raise CampusOpsError(ErrorCode.DATABASE_ERROR, "修改后的预约读回不一致，事务已回滚。")
                    events.invalidate_pending(connection, exclude_change=change_id)
                decision = ReservationChangeDecision(change_id=change_id, status=status, reservation=updated,
                    actor_alias=actor, reason=rejection.strip() if rejection is not None else "批准预约修改" if not stale else "资源或预约状态已变化",
                    decided_at=repo.now_utc(), message="预约修改已批准并保存。" if status == "APPLIED" else "修改方案已拒绝，原预约保留。" if status == "REJECTED" else "方案已过期，本次没有修改预约。",
                    error=ServiceError(code=ErrorCode.SNAPSHOT_CONFLICT, message="请在最新资源状态下重新生成修改方案。") if status == "STALE" else None)
                events.change_event(connection, change_id, "RESERVATION_CHANGE_DECIDED", {"key": identity,
                    "operation": "reject" if rejection is not None else "approve", "decision": decision.model_dump(mode="json")}, actor_type="administrator")
                return decision
        except sqlite3.Error:
            raise CampusOpsError(ErrorCode.DATABASE_ERROR, "修改审批事务未完成，所有变更已回滚。") from None

    def approve(self, proposal_id: str, actor_alias: str, idempotency_key: str) -> ApplyResult:
        result = self._approve(proposal_id, actor_alias, idempotency_key)
        self._record_failed_decision(result)
        return result

    def _record_failed_decision(self, result: ApplyResult) -> None:
        if result.error is None:
            return
        # The failed transaction has already ended. Persist a separate failure fact,
        # never a success audit from a rolled-back booking transaction.
        try:
            with self._database.transaction() as connection:
                repo.audit(connection, "APPROVAL_FAILED", "proposal", result.proposal_id, {},
                           {"status": result.status, "error_code": result.error.code}, actor_type="administrator")
        except sqlite3.Error:
            logging.getLogger(__name__).error("Approval failed and audit DB unavailable: code=%s", result.error.code)

    def _approve(self, proposal_id: str, actor_alias: str, idempotency_key: str) -> ApplyResult:
       actor_alias = actor_alias.strip() if isinstance(actor_alias, str) else ""
        if not actor_alias or len(actor_alias) > 100 or not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
            return failure(proposal_id, ErrorCode.INVALID_INPUT, "Actor and a stable idempotency key are required")
        try:
            with self._database.transaction() as connection:
                by_key = connection.execute("SELECT * FROM approvals WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if by_key:
                    if by_key["proposal_id"] != proposal_id or by_key["decision"] != "APPROVE" or by_key["actor_alias"] != actor_alias:
                        return failure(proposal_id, ErrorCode.IDEMPOTENCY_CONFLICT, "Key is bound to a different approval request")
                    return ApplyResult.model_validate_json(by_key["result_json"])
                previous = connection.execute("SELECT * FROM approvals WHERE proposal_id=?", (proposal_id,)).fetchone()
                if previous:
                    return ApplyResult.model_validate_json(previous["result_json"])
                proposal = repo.get_proposal(connection, proposal_id)
                if proposal.status == ProposalStatus.STALE:
                    return failure(proposal_id, ErrorCode.SNAPSHOT_CONFLICT, "Proposal is stale; start a new workflow", status=ApplyStatus.STALE)
                if proposal.status != ProposalStatus.VALIDATED or not proposal.validation.passed:
                    return failure(proposal_id, ErrorCode.INVALID_STATE, "Only a validated pending proposal can be approved")

                current = repo.snapshot(connection, rules=self._database.get_booking_rules())
                validation = validate_plan(current, proposal.incident_id, proposal.changes)
                if current.fingerprint != proposal.snapshot_fingerprint or not validation.passed:
                    connection.execute("UPDATE proposals SET status='STALE' WHERE id=?", (proposal_id,))
                    connection.execute("UPDATE workflow_runs SET status='STALE' WHERE id=?", (proposal.run_id,))
                    repo.audit(connection, "APPROVAL_STALE", "proposal", proposal_id,
                               {"fingerprint": proposal.snapshot_fingerprint},
                               {"fingerprint": current.fingerprint, "validation": validation.model_dump(mode="json")},
                               actor_type="administrator")
                    return failure(proposal_id, ErrorCode.SNAPSHOT_CONFLICT,
                                   "Resource snapshot or constraints changed; no reservations were changed", status=ApplyStatus.STALE)

                connection.execute("UPDATE workflow_runs SET status='APPLYING' WHERE id=?", (proposal.run_id,))
                before = {row.id: row for row in current.reservations}
                for change in proposal.changes:
                    cursor = connection.execute(
                        "UPDATE reservations SET equipment_id=?,version=version+1,updated_at_utc=? "
                        "WHERE id=? AND version=? AND equipment_id=? AND status='ACTIVE'",
                        (change.to_equipment_id, repo.timestamp(), change.reservation_id,
                         change.expected_reservation_version, change.from_equipment_id),
                    )
                    if cursor.rowcount != 1:
                        raise CampusOpsError(ErrorCode.SNAPSHOT_CONFLICT, "Booking changed during approval")
                # Read back within the same transaction; any mismatch aborts all updates.
                after = {row.id: row for row in repo.list_reservations(connection)}
                for change in proposal.changes:
                    observed = after[change.reservation_id]
                    if observed.equipment_id != change.to_equipment_id or observed.version != change.expected_reservation_version + 1:
                        raise CampusOpsError(ErrorCode.DATABASE_ERROR, "Post-write verification failed")
                result = ApplyResult(
                    proposal_id=proposal_id, status=ApplyStatus.APPLIED,
                    changed_reservation_ids=tuple(item.reservation_id for item in proposal.changes),
                    message="Approved and atomically applied to fictional bookings",
                )
                connection.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?)", (
                    repo.new_id("approval"), proposal_id, actor_alias, "APPROVE", idempotency_key,
                    "", result.model_dump_json(), repo.timestamp(),
                ))
                connection.execute("UPDATE proposals SET status='APPLIED' WHERE id=?", (proposal_id,))
                connection.execute("UPDATE workflow_runs SET status='APPLIED',finished_at_utc=? WHERE id=?",
                                   (repo.timestamp(), proposal.run_id))
                events.invalidate_pending(connection)
                repo.audit(connection, "PROPOSAL_APPLIED", "proposal", proposal_id,
                           {item.reservation_id: before[item.reservation_id].model_dump(mode="json") for item in proposal.changes},
                           {item.reservation_id: after[item.reservation_id].model_dump(mode="json") for item in proposal.changes},
                           actor_type="administrator")
                return result
        except CampusOpsError as exc:
            return failure(proposal_id, exc.error.code, exc.error.message)
        except sqlite3.Error as exc:
            code = ErrorCode.DATABASE_BUSY if getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else ErrorCode.DATABASE_ERROR
            return failure(proposal_id, code, "Approval database transaction failed and was rolled back")

    def reject(self, proposal_id: str, actor_alias: str, reason: str) -> ApplyResult:
        result = self._reject(proposal_id, actor_alias, reason)
        self._record_failed_decision(result)
        return result

    def _reject(self, proposal_id: str, actor_alias: str, reason: str) -> ApplyResult:
        if not actor_alias.strip() or len(actor_alias) > 100 or not reason.strip() or len(reason) > 500:
            return failure(proposal_id, ErrorCode.INVALID_INPUT, "Actor and rejection reason are required")
        try:
            with self._database.transaction() as connection:
                previous = connection.execute("SELECT * FROM approvals WHERE proposal_id=?", (proposal_id,)).fetchone()
                if previous:
                    if previous["decision"] == "REJECT":
                        return ApplyResult.model_validate_json(previous["result_json"])
                    return failure(proposal_id, ErrorCode.ALREADY_DECIDED, "An applied proposal cannot be rejected")
                proposal = repo.get_proposal(connection, proposal_id)
                if proposal.status not in (ProposalStatus.PROPOSED, ProposalStatus.VALIDATED):
                    return failure(proposal_id, ErrorCode.INVALID_STATE, "Proposal is not awaiting a decision")
                result = ApplyResult(proposal_id=proposal_id, status=ApplyStatus.REJECTED,
                                     message="Administrator rejected the proposal; bookings were unchanged")
                connection.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?)", (
                    repo.new_id("approval"), proposal_id, actor_alias, "REJECT", "reject:"+proposal_id,
                    reason, result.model_dump_json(), repo.timestamp(),
                ))
                connection.execute("UPDATE proposals SET status='REJECTED' WHERE id=?", (proposal_id,))
                connection.execute("UPDATE workflow_runs SET status='REJECTED',finished_at_utc=? WHERE id=?",
                                   (repo.timestamp(), proposal.run_id))
                repo.audit(connection, "PROPOSAL_REJECTED", "proposal", proposal_id, {},
                           {"actor_alias": actor_alias, "reason": reason}, actor_type="administrator")
                return result
        except CampusOpsError as exc:
            return failure(proposal_id, exc.error.code, exc.error.message)
        except sqlite3.Error:
            return failure(proposal_id, ErrorCode.DATABASE_ERROR, "Rejection transaction failed and was rolled back")
