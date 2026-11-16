"""Explicit, offline fictional-data bootstrap; never an Agent tool."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import AgentConfig, Equipment, ErrorCode, Reservation
from campusops.storage.database import Database
from campusops.storage.repository import audit, canonical_json, timestamp

DATASET_ID = "campusops-fictional-demo-v1"
RESET_CONFIRMATION = "RESET_FICTIONAL_DEMO"


def seed_demo(database: Database, asset_path: Path, *, confirmation: str | None = None) -> None:
    payload = json.loads(asset_path.read_text(encoding="utf-8"))
    if payload.get("dataset_id") != DATASET_ID or payload.get("fictional") is not True:
        raise CampusOpsError(ErrorCode.INVALID_INPUT, "Only the known fictional dataset may be seeded")
    equipment = tuple(Equipment.model_validate(row) for row in payload["equipment"])
    reservations = tuple(Reservation.model_validate(row) for row in payload["reservations"])
    registry = tuple(AgentConfig.model_validate(row) for row in payload["agent_registry"])
    with database.transaction() as connection:
        marker = connection.execute("SELECT value FROM project_metadata WHERE key='dataset_id'").fetchone()
        if marker and confirmation != RESET_CONFIRMATION:
            raise CampusOpsError(ErrorCode.INVALID_STATE, "Demo already exists; reset requires explicit confirmation")
        if marker and marker[0] != DATASET_ID:
            raise CampusOpsError(ErrorCode.INVALID_STATE, "Refusing to reset an unknown dataset")
        counts = sum(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
            "equipment", "reservations", "incidents", "agent_registry", "workflow_runs", "approvals", "audit_log",
        ))
        if counts and not marker:
            raise CampusOpsError(ErrorCode.INVALID_STATE, "Refusing to replace unrecognized existing data")
        if marker:
            # Backup under the same D-drive data directory; no removal/overwrite.
            backup = database.path.with_name(database.path.stem + ".backup-" +
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8] + ".db")
            with database.connect(readonly=True) as source:
                destination = sqlite3.connect(backup)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
            for table in ("approvals", "proposal_items", "proposals", "run_steps", "workflow_runs",
                          "incidents", "reservations", "agent_registry", "equipment", "audit_log"):
                connection.execute(f"DELETE FROM {table}")
        connection.execute("INSERT OR REPLACE INTO project_metadata VALUES ('dataset_id',?)", (DATASET_ID,))
        for row in equipment:
            connection.execute("INSERT INTO equipment VALUES (?,?,?,?,?,?,?)", (
                row.id, row.name, row.category, canonical_json(row.capabilities), row.status,
                row.version, timestamp(row.updated_at),
            ))
        for row in reservations:
            connection.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?,?)", (
                row.id, row.equipment_id, row.borrower_alias, timestamp(row.starts_at), timestamp(row.ends_at),
                canonical_json(row.required_capabilities), row.status, row.version, timestamp(row.updated_at),
            ))
        for row in registry:
            connection.execute("INSERT INTO agent_registry VALUES (?,?,?,?,?,?,?,?)", (
                row.agent_id, row.display_name, row.role, canonical_json(row.skills), canonical_json(row.allowed_tools),
                int(row.enabled), row.prompt_version, timestamp(row.updated_at),
            ))
        audit(connection, "DEMO_SEEDED" if not marker else "DEMO_RESET", "dataset", DATASET_ID,
              {}, {"equipment": len(equipment), "reservations": len(reservations), "fictional": True})
