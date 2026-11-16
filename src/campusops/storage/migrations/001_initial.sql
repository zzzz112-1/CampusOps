CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL
);
CREATE TABLE project_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE equipment (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('AVAILABLE','FAULTED','MAINTENANCE')),
    version INTEGER NOT NULL CHECK(version >= 1), updated_at_utc TEXT NOT NULL
);
CREATE TABLE reservations (
    id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL REFERENCES equipment(id),
    borrower_alias TEXT NOT NULL, starts_at_utc TEXT NOT NULL, ends_at_utc TEXT NOT NULL,
    requirements_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','CANCELLED')),
    version INTEGER NOT NULL CHECK(version >= 1), updated_at_utc TEXT NOT NULL,
    CHECK(starts_at_utc < ends_at_utc)
);
CREATE INDEX ix_reservations_window ON reservations(equipment_id,status,starts_at_utc,ends_at_utc);
CREATE TABLE incidents (
    id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL REFERENCES equipment(id),
    kind TEXT NOT NULL CHECK(kind = 'equipment_failure'), effective_at_utc TEXT NOT NULL,
    reason TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('OPEN','RESOLVED')),
    created_at_utc TEXT NOT NULL
);
CREATE INDEX ix_incidents_equipment_status ON incidents(equipment_id,status);
CREATE TABLE agent_registry (
    agent_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, role TEXT NOT NULL,
    skills_json TEXT NOT NULL, allowed_tools_json TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), prompt_version TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE workflow_runs (
    id TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id), status TEXT NOT NULL,
    execution_mode TEXT NOT NULL, provider TEXT, model_id TEXT,
    model_calls INTEGER NOT NULL DEFAULT 0 CHECK(model_calls >= 0),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 2),
    snapshot_fingerprint TEXT NOT NULL, created_at_utc TEXT NOT NULL, finished_at_utc TEXT,
    result_json TEXT, error_json TEXT
);
CREATE TABLE run_steps (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(id),
    seq INTEGER NOT NULL CHECK(seq >= 1), agent_id TEXT REFERENCES agent_registry(agent_id),
    node TEXT NOT NULL, tool_name TEXT, decision_reason TEXT NOT NULL,
    input_digest TEXT NOT NULL, output_digest TEXT NOT NULL,
    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0), error TEXT,
    model_calls INTEGER NOT NULL CHECK(model_calls BETWEEN 0 AND 1),
    retry_count INTEGER NOT NULL CHECK(retry_count BETWEEN 0 AND 2),
    status TEXT, execution_mode TEXT NOT NULL, created_at_utc TEXT NOT NULL,
    UNIQUE(run_id,seq)
);
CREATE TABLE proposals (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id),
    incident_id TEXT NOT NULL REFERENCES incidents(id),
    status TEXT NOT NULL CHECK(status IN ('PROPOSED','VALIDATED','APPLIED','REJECTED','STALE')),
    snapshot_fingerprint TEXT NOT NULL, score REAL NOT NULL DEFAULT 0,
    reasoning TEXT NOT NULL, validation_json TEXT NOT NULL, created_at_utc TEXT NOT NULL
);
CREATE INDEX ix_proposals_run ON proposals(run_id);
CREATE TABLE proposal_items (
    id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL REFERENCES proposals(id),
    reservation_id TEXT NOT NULL REFERENCES reservations(id),
    old_equipment_id TEXT NOT NULL REFERENCES equipment(id),
    new_equipment_id TEXT NOT NULL REFERENCES equipment(id),
    starts_at_utc TEXT NOT NULL, ends_at_utc TEXT NOT NULL,
    reservation_version INTEGER NOT NULL CHECK(reservation_version >= 1),
    UNIQUE(proposal_id,reservation_id), CHECK(starts_at_utc < ends_at_utc)
);
CREATE TABLE approvals (
    id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE REFERENCES proposals(id),
    actor_alias TEXT NOT NULL, decision TEXT NOT NULL CHECK(decision IN ('APPROVE','REJECT')),
    idempotency_key TEXT NOT NULL UNIQUE, reason TEXT NOT NULL,
    result_json TEXT NOT NULL, created_at_utc TEXT NOT NULL
);
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY, actor_type TEXT NOT NULL, action TEXT NOT NULL,
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at_utc TEXT NOT NULL
);
