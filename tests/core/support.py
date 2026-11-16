"""Explicit offline_mock_test injections, containing fictional anonymous data only."""

from datetime import datetime, timedelta, timezone
import json
import threading

from campusops.contracts.models import (
    AgentConfig, BookingRules, Equipment, ExecutionMode, Incident, ModelResponse,
    ModelUsage, Reservation, ResourceSnapshot, RunStep, ValidationResult,
)


NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)
TOOLS = ("get_incident", "get_impacted_reservations", "get_booking_rules", "append_run_step")


def equipment(identifier, **patch):
    return Equipment.model_validate({
        "id": identifier, "name": "Fictional camera", "category": "camera",
        "capabilities": ("photo",), "status": "AVAILABLE", "version": 1, "updated_at": NOW,
        **patch,
    })


def reservation(identifier, start=10, **patch):
    return Reservation.model_validate({
        "id": identifier, "equipment_id": "broken", "borrower_alias": "Fictional-01",
        "starts_at": NOW + timedelta(hours=start), "ends_at": NOW + timedelta(hours=start + 1),
        "required_capabilities": ("photo",), "status": "ACTIVE", "version": 1, "updated_at": NOW,
        **patch,
    })


def registry():
    return tuple(AgentConfig(
        agent_id=role + "-agent", display_name=role, role=role, skills=(skill,),
        allowed_tools=TOOLS if role == "impact" else TOOLS + ("list_equipment_candidates", "validate_candidate_plan"),
        enabled=True, prompt_version="v1", updated_at=NOW,
    ) for role, skill in (("impact", "impact_analysis"), ("planner", "scheduling"), ("verifier", "verification")))


class MemoryReadonly:
    """TEST_INJECTED port. Its validation result is a controlled test input.

    Real Main validation is exercised separately via the public composition factory.
    This port has no database, SQL, booking mutation or production fallback.
    """
    execution_mode = ExecutionMode.OFFLINE_MOCK_TEST

    def __init__(self, count=1, run_id="run_test"):
        self.run_id = run_id
        self.incident = Incident(id="incident_test", equipment_id="broken", effective_at=NOW,
                                 reason="Fictional fault", created_at=NOW)
        self.impacted = tuple(reservation(f"booking_{index}", start=10 + index) for index in range(count))
        self.equipment = (equipment("broken", status="FAULTED"), equipment("camera_a"), equipment("camera_b"))
        self.snapshot = ResourceSnapshot(fingerprint="a" * 64, equipment=self.equipment,
                                         reservations=self.impacted, incidents=(self.incident,))
        self.configs = registry()
        self.candidates = {row.id: self.equipment[1:] for row in self.impacted}
        self.steps = []
        self.validation_calls = []
        self.validation = ValidationResult(passed=True, evidence_ids=("snapshot:" + self.snapshot.fingerprint,))
        self.daily_calls = 0
        self.barrier = None
        self.max_active = 0
        self._active = 0
        self._seen = set()
        self._lock = threading.Lock()

    def get_incident(self, incident_id):
        assert incident_id == self.incident.id
        return self.incident

    def get_impacted_reservations(self, incident_id):
        assert incident_id == self.incident.id
        return self.impacted

    def list_equipment_candidates(self, reservation_id):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            first = reservation_id not in self._seen
            self._seen.add(reservation_id)
        try:
            if first and self.barrier:
                self.barrier.wait(timeout=5)
            return self.candidates[reservation_id]
        finally:
            with self._lock:
                self._active -= 1

    def get_booking_rules(self):
        return BookingRules()

    def get_snapshot(self):
        return self.snapshot

    def list_agent_configs(self):
        return self.configs

    def get_model_usage(self):
        return ModelUsage(utc_date=datetime.now(timezone.utc).date().isoformat(), daily_model_calls=self.daily_calls)

    def validate_candidate_plan(self, changes):
        self.validation_calls.append(changes)
        return self.validation

    def append_run_step(self, step):
        assert step.run_id == self.run_id
        with self._lock:
            result = RunStep(**step.model_dump(), id=f"step_{len(self.steps) + 1}", seq=len(self.steps) + 1,
                             execution_mode=self.execution_mode, created_at=datetime.now(timezone.utc))
            self.steps.append(result)
            return result


class InjectedModel:
    """TEST_INJECTED / offline_mock_test, never used by production code."""
    def __init__(self, actions=None):
        self.requests = []
        self.actions = actions or {}
        self.counts = {}

    def complete(self, request):
        self.requests.append(request)
        title = json.loads(request.response_schema)["title"]
        index = self.counts.get(title, 0)
        self.counts[title] = index + 1
        payload = json.loads(request.messages[1].content)
        if len(request.messages) > 2:
            # TEST_INJECTED clients see only observations actually sent in the
            # follow-up request; the fixture does not manufacture tool results.
            payload = {**payload, **json.loads(request.messages[-1].content)}
        if title == "ImpactReport":
            result = {"reservation_ids": [row["id"] for row in payload["reservations"]],
                      "evidence_ids": [payload["evidence_id"]], "summary": "TEST_INJECTED impact", "tools": []}
        elif title == "PlanningDecision":
            result = {"plan_id": payload["offered_plans"][0]["plan_id"], "reason_codes": ["stable_rank"],
                      "summary": "TEST_INJECTED plan", "tools": []}
        else:
            result = {"verdict": "pass", "reservation_ids": [row["id"] for row in payload["reservations"]],
                      "evidence_ids": [payload["evidence_id"]], "reason_codes": ["constraints_satisfied"],
                      "summary": "TEST_INJECTED verification", "tools": []}
        action = self.actions.get(title)
        if action:
            result = action(result, payload, index)
        if isinstance(result, Exception):
            raise result
        return ModelResponse(content=result if isinstance(result, str) else json.dumps(result),
                             provider="TEST_INJECTED", model_id="offline_mock_test", input_tokens=3, output_tokens=2)
