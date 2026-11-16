"""Catalog metadata in existing audit events; no schema change."""
import json
from campusops.contracts.models import EquipmentCatalogEntry,EquipmentCatalogMutationResult
from campusops.storage import repository as repo


def states(connection):
    values={}
    for row in connection.execute("SELECT entity_id,after_json FROM audit_log WHERE entity_type='equipment_catalog' ORDER BY rowid"):
        values[row[0]]=json.loads(row[1])["record"]
    return values


def archived_ids(connection):
    return frozenset(key for key,value in states(connection).items() if value["archived"])


def entries(connection, include_archived=False):
    saved=states(connection);result=[]
    for item in repo.list_equipment(connection):
        previous=saved.get(item.id,{})
        record=EquipmentCatalogEntry(equipment=item,archived=previous.get("archived",False),
            archived_at=previous.get("archived_at"),archive_previous_status=previous.get("archive_previous_status"))
        if include_archived or not record.archived:result.append(record)
    return tuple(result)


def command(connection,identity):
    row=connection.execute("SELECT after_json FROM audit_log WHERE entity_type='equipment_command' AND entity_id=? ORDER BY rowid LIMIT 1",(identity,)).fetchone()
    return json.loads(row[0]) if row else None


def saved_result(value):
    return EquipmentCatalogMutationResult.model_validate(value["result"])
