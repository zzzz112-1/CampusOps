"""Local inventory commands; never changes reservations or calls models."""
from hashlib import sha256
import json,sqlite3
from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import (EquipmentCreate,EquipmentUpdate,EquipmentArchiveCreate,EquipmentRestoreCreate,
    EquipmentCatalogEntry,EquipmentCatalogMutationResult,ErrorCode)
from campusops.storage import repository as repo
from campusops.storage import catalog as records
from campusops.storage.reservation_events import invalidate_pending,key_digest


class EquipmentCatalogService:
    def __init__(self,database):self.database=database

    def list(self,include_archived=False):
        with self.database.connect(readonly=True) as connection:return records.entries(connection,include_archived)

    def find(self,key):
        with self.database.connect(readonly=True) as connection:value=records.command(connection,key_digest(key))
        return records.saved_result(value) if value else None

    def mutate(self,operation,request):
        types={"create":EquipmentCreate,"update":EquipmentUpdate,"archive":EquipmentArchiveCreate,"restore":EquipmentRestoreCreate}
        request=types[operation].model_validate(request)
        identity=key_digest(request.idempotency_key);fingerprint=sha256(request.model_dump_json().encode('utf-8')).hexdigest()
        try:
            with self.database.transaction() as connection:
                previous=records.command(connection,identity)
                if previous:
                    if previous['operation']!=operation or previous['request_hash']!=fingerprint:
                        raise CampusOpsError(ErrorCode.IDEMPOTENCY_CONFLICT,"此请求标识已用于不同器材操作。")
                    return records.saved_result(previous)
                before=None;prior_status=None;archived_at=None;archived=False
                if operation!='create':
                    before=next((row for row in records.entries(connection,True) if row.equipment.id==request.equipment_id),None)
                    if before is None:raise CampusOpsError(ErrorCode.NOT_FOUND,"未找到指定器材。")
                    if before.equipment.version!=request.expected_equipment_version:
                        raise CampusOpsError(ErrorCode.SNAPSHOT_CONFLICT,"器材已被其他操作更新，请刷新后重新核对。")
                    if before.archived != (operation=='restore'):
                        raise CampusOpsError(ErrorCode.INVALID_STATE,"已移除器材只能在已移除列表恢复；在用器材不能重复恢复。")
                    active=tuple(row for row in repo.list_reservations(connection) if row.equipment_id==request.equipment_id and row.status=='ACTIVE')
                    if operation=='archive' and active:
                        raise CampusOpsError(ErrorCode.INVALID_STATE,f"此器材仍有{len(active)}条有效预约（如{active[0].id}）。请先调整或取消这些预约，再移除；不会自动改预约。")
                    if operation=='update':
                        if active and request.category.strip()!=before.equipment.category:
                            raise CampusOpsError(ErrorCode.INVALID_STATE,"此器材仍有关联的有效预约，不能改变类别；请先处理预约。")
                        broken=next((row for row in active if not set(row.required_capabilities)<=set(request.capabilities)),None)
                        if broken:raise CampusOpsError(ErrorCode.CAPABILITY_MISMATCH,"新能力不能满足有效预约"+broken.id+"，请先调整该预约。")
                moment=repo.now_utc()
                if operation=='create':
                    equipment_id=repo.new_id('gear')
                    connection.execute("INSERT INTO equipment VALUES (?,?,?,?,?,?,?)",(equipment_id,request.name.strip(),request.category.strip(),json.dumps(request.capabilities),"AVAILABLE",1,repo.timestamp(moment)))
                else:
                    equipment_id=request.equipment_id
                    if operation=='update':
                        if (request.name.strip(),request.category.strip(),request.capabilities)==(before.equipment.name,before.equipment.category,before.equipment.capabilities):
                            raise CampusOpsError(ErrorCode.INVALID_INPUT,"器材内容没有变化，无需保存。")
                        connection.execute("UPDATE equipment SET name=?,category=?,capabilities_json=?,version=version+1,updated_at_utc=? WHERE id=? AND version=?",(request.name.strip(),request.category.strip(),json.dumps(request.capabilities),repo.timestamp(moment),equipment_id,before.equipment.version))
                    else:
                        archived=operation=='archive';prior_status=before.equipment.status if archived else None;archived_at=moment if archived else None
                        status='MAINTENANCE' if archived else before.archive_previous_status
                        if status is None:raise CampusOpsError(ErrorCode.INVALID_STATE,"移除前状态缺失，不能自动恢复。")
                        connection.execute("UPDATE equipment SET status=?,version=version+1,updated_at_utc=? WHERE id=? AND version=?",(status,repo.timestamp(moment),equipment_id,before.equipment.version))
                observed=next(row for row in repo.list_equipment(connection) if row.id==equipment_id)
                if operation=='create':correct=observed.name==request.name.strip() and observed.category==request.category.strip() and observed.capabilities==request.capabilities and observed.status=='AVAILABLE' and observed.version==1
                else:
                    correct=observed.version==before.equipment.version+1
                    if operation=='update':correct=correct and observed.name==request.name.strip() and observed.category==request.category.strip() and observed.capabilities==request.capabilities and observed.status==before.equipment.status
                    else:correct=correct and observed.status==status
                if not correct:raise CampusOpsError(ErrorCode.DATABASE_ERROR,"器材保存读回不一致，事务已回滚。")
                record=EquipmentCatalogEntry(equipment=observed,archived=archived,archived_at=archived_at,archive_previous_status=prior_status)
                invalidated=invalidate_pending(connection)
                message={"create":"器材已新增并保存。","update":"器材信息已更新。","archive":"器材已移除到归档，历史记录保留。","restore":"器材已恢复到台账，保留移除前的设备状态。"}[operation]
                result=EquipmentCatalogMutationResult(record=record,invalidated_proposal_ids=invalidated,message=message)
                repo.audit(connection,'EQUIPMENT_'+operation.upper(),'equipment_catalog',equipment_id,
                    before.model_dump(mode='json') if before else {},{'record':record.model_dump(mode='json'),'actor_alias':request.actor_alias,
                    'note':request.note if operation in ('create','update') else request.reason},actor_type='administrator')
                repo.audit(connection,'EQUIPMENT_COMMAND_SAVED','equipment_command',identity,{},
                    {'operation':operation,'request_hash':fingerprint,'result':result.model_dump(mode='json')},actor_type='administrator')
                return result
        except sqlite3.Error:
            raise CampusOpsError(ErrorCode.DATABASE_ERROR,"器材事务未完成，变更已回滚。请刷新核对。") from None
