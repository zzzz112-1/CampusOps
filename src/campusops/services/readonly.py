"""Run-bound snapshot tools, with only a scoped append-only audit operation."""

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import (
    AgentConfig, BookingRules, ErrorCode, ModelUsage, ProposalItem, ResourceSnapshot,
    RunStatus, RunStepCreate, ValidationResult,
)
from campusops.domain.validation import candidates, impacted_reservations, validate_plan
from campusops.storage import repository as repo
from campusops.storage.reservation_events import extra_model_calls
from campusops.storage.database import Database


ACTIVE_STEP_STATUSES = {
    RunStatus.SCOPING, RunStatus.PLANNING, RunStatus.VERIFYING, RunStatus.REPLANNING,
}


class ReadonlyTools:
    def __init__(self, database: Database, run_id: str, incident_id: str, snapshot: ResourceSnapshot,
                 *, rules: BookingRules | None = None, agent_configs: tuple[AgentConfig, ...] | None = None):
        self.__database = database
        self.__run_id = run_id
        self.__incident_id = incident_id
        self.__snapshot = snapshot
        self.__rules = rules if rules is not None else database.get_booking_rules()
        if agent_configs is None:
            with database.connect(readonly=True) as connection:
                agent_configs = repo.list_agent_configs(connection)
        self.__agent_configs = tuple(AgentConfig.model_validate(row) for row in agent_configs)

    def get_incident(self, incident_id: str):
        if incident_id != self.__incident_id:
            raise CampusOpsError(ErrorCode.INVALID_INPUT, "Tool is bound to another incident")
        for item in self.__snapshot.incidents:
            if item.id == incident_id:
                return item
        raise CampusOpsError(ErrorCode.NOT_FOUND, "Incident not found")

    def get_impacted_reservations(self, incident_id: str):
        return impacted_reservations(self.__snapshot, self.get_incident(incident_id))

    def list_equipment_candidates(self, reservation_id: str):
        booking = next((row for row in self.get_impacted_reservations(self.__incident_id) if row.id == reservation_id), None)
        if booking is None:
            raise CampusOpsError(ErrorCode.NOT_FOUND, "Booking is not in this incident's impact set")
        return candidates(self.__snapshot, booking)

    def get_booking_rules(self):
        return self.__rules

    def get_snapshot(self):
        return self.__snapshot

    def list_agent_configs(self):
        return self.__agent_configs

    def get_model_usage(self):
        utc_date = repo.now_utc().date().isoformat()
        with self.__database.connect(readonly=True) as connection:
            count = connection.execute(
                "SELECT COALESCE(SUM(model_calls),0) FROM run_steps "
                "WHERE substr(created_at_utc,1,10)=? AND execution_mode='real_api'",
                (utc_date,),
            ).fetchone()[0]
            count += extra_model_calls(connection, utc_date)
        return ModelUsage(utc_date=utc_date, daily_model_calls=count)

    def validate_candidate_plan(self, changes: tuple[ProposalItem, ...]) -> ValidationResult:
        changes = tuple(ProposalItem.model_validate(item) for item in changes)
        return validate_plan(self.__snapshot, self.__incident_id, changes)

    def append_run_step(self, step: RunStepCreate):
        step = RunStepCreate.model_validate(step)
        if step.run_id != self.__run_id:
            raise CampusOpsError(ErrorCode.INVALID_INPUT, "Cannot append to another run")
        if step.status is not None and step.status not in ACTIVE_STEP_STATUSES:
            raise CampusOpsError(ErrorCode.INVALID_STATE, "Only Main may finalize workflow/approval status")
        with self.__database.transaction() as connection:
            run = repo.get_run(connection, step.run_id)
            if run.status not in ({RunStatus.CREATED} | ACTIVE_STEP_STATUSES):
                raise CampusOpsError(ErrorCode.INVALID_STATE, "Cannot append to a finalized run")
            if step.agent_id is not None:
                agent = next((row for row in self.__agent_configs if row.agent_id == step.agent_id), None)
                if agent is None or not agent.enabled or (step.tool_name and step.tool_name not in agent.allowed_tools):
                    raise CampusOpsError(ErrorCode.INVALID_STATE, "Agent or requested tool is not enabled")
            seq = connection.execute("SELECT COALESCE(MAX(seq),0)+1 FROM run_steps WHERE run_id=?", (step.run_id,)).fetchone()[0]
            step_id = repo.new_id("step")
            connection.execute("INSERT INTO run_steps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                step_id, step.run_id, seq, step.agent_id, step.node, step.tool_name, step.decision_reason,
                step.input_digest, step.output_digest, step.duration_ms,
                step.error.model_dump_json() if step.error else None, step.model_calls, step.retry_count,
                step.status, run.execution_mode, repo.timestamp(),
            ))
            connection.execute(
                "UPDATE workflow_runs SET model_calls=model_calls+?,retry_count=MAX(retry_count,?),status=? WHERE id=?",
                (step.model_calls, step.retry_count, step.status or run.status, step.run_id),
            )
            return next(row for row in repo.list_run_steps(connection, step.run_id) if row.id == step_id)
