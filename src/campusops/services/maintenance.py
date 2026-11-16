"""Owner-confirmed repair transaction. Never changes any reservation."""

from hashlib import sha256
import json
import sqlite3

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import EquipmentRecoveryCreate, EquipmentRecoveryResult, ErrorCode
from campusops.storage import repository as repo
from campusops.storage.database import Database
from campusops.storage.catalog import archived_ids


class EquipmentMaintenanceService:
    def __init__(self, database: Database):
        self._database = database

    def recover(self, request: EquipmentRecoveryCreate) -> EquipmentRecoveryResult:
        request = EquipmentRecoveryCreate.model_validate(request)
        identity = sha256(request.idempotency_key.encode("utf-8")).hexdigest()
        payload_hash = sha256(request.model_dump_json().encode("utf-8")).hexdigest()
        try:
            with self._database.transaction() as connection:
                previous = connection.execute(
                    "SELECT after_json FROM audit_log WHERE action='EQUIPMENT_RECOVERED' "
                    "AND entity_type='equipment_recovery' AND entity_id=?", (identity,),
                ).fetchone()
                if previous:
                    saved = json.loads(previous[0])
                    if saved["request_hash"] != payload_hash:
                        raise CampusOpsError(ErrorCode.IDEMPOTENCY_CONFLICT, "此维修请求标识已用于不同操作，请刷新核对。")
                    return EquipmentRecoveryResult.model_validate(saved["result"])
                equipment = next((row for row in repo.list_equipment(connection)
                                  if row.id == request.equipment_id), None)
                if equipment is None:
                    raise CampusOpsError(ErrorCode.NOT_FOUND, "未找到要恢复的器材。")
                if equipment.id in archived_ids(connection):
                    raise CampusOpsError(ErrorCode.INVALID_STATE,"已移除器材不能直接维修放行，请先恢复台账。")
                if equipment.version != request.expected_equipment_version:
                    raise CampusOpsError(ErrorCode.SNAPSHOT_CONFLICT, "器材状态已变化，请刷新后重新确认维修结果。")
                if equipment.status not in ("FAULTED", "MAINTENANCE"):
                    raise CampusOpsError(ErrorCode.INVALID_STATE, "只有故障或维护中的器材可以执行维修恢复。")
                incidents = tuple(row.id for row in repo.list_incidents(connection)
                                  if row.equipment_id == equipment.id and row.status == "OPEN")
                pending = connection.execute(
                    "SELECT id,run_id FROM proposals WHERE status IN ('PROPOSED','VALIDATED') ORDER BY id"
                ).fetchall()
                moment = repo.now_utc()
                connection.execute(
                    "UPDATE equipment SET status='AVAILABLE',version=version+1,updated_at_utc=? WHERE id=? AND version=?",
                    (repo.timestamp(moment), equipment.id, equipment.version),
                )
                connection.execute("UPDATE incidents SET status='RESOLVED' WHERE equipment_id=? AND status='OPEN'",
                                   (equipment.id,))
                # Every pending proposal binds the complete resource snapshot. A repair
                # changes it, even if this equipment is not one of its replacements.
                for row in pending:
                    connection.execute("UPDATE proposals SET status='STALE' WHERE id=?", (row["id"],))
                    connection.execute("UPDATE workflow_runs SET status='STALE',finished_at_utc=? WHERE id=? AND status='WAIT_APPROVAL'",
                                       (repo.timestamp(moment), row["run_id"]))
                observed = next(row for row in repo.list_equipment(connection) if row.id == equipment.id)
                from campusops.storage.reservation_events import invalidate_pending
                all_invalidated=invalidate_pending(connection)
                result = EquipmentRecoveryResult(
                    equipment=observed, resolved_incident_ids=incidents,
                    invalidated_proposal_ids=tuple(dict.fromkeys((*tuple(row["id"] for row in pending),*all_invalidated))), recovered_at=moment,
                )
                repo.audit(connection, "EQUIPMENT_RECOVERED", "equipment_recovery", identity,
                           {"equipment": equipment.model_dump(mode="json"), "open_incident_ids": incidents},
                           {"request_hash": payload_hash, "actor_alias": request.actor_alias, "note": request.note,
                            "result": result.model_dump(mode="json")}, actor_type="administrator")
                return result
        except sqlite3.Error:
            raise CampusOpsError(ErrorCode.DATABASE_ERROR, "维修恢复事务未完成，所有变更已回滚。请刷新核对。") from None
