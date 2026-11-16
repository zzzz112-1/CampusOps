"""Private LLM response schemas, not a second public business contract."""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from campusops.contracts.models import Identifier


class RoleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolRequest(RoleOutput):
    name: str = Field(min_length=1, max_length=100)
    reservation_id: Identifier | None


class ImpactReport(RoleOutput):
    reservation_ids: tuple[Identifier, ...] = Field(max_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    summary: str = Field(min_length=1, max_length=800)
    tools: tuple[ToolRequest, ...] = Field(max_length=6)


class PlanningDecision(RoleOutput):
    plan_id: str = Field(min_length=1, max_length=100)
    reason_codes: tuple[Literal[
        "preserve_times", "capabilities_match", "minimize_category_change", "stable_rank",
    ], ...] = Field(min_length=1, max_length=4)
    summary: str = Field(min_length=1, max_length=800)
    tools: tuple[ToolRequest, ...] = Field(max_length=6)


class VerificationReport(RoleOutput):
    verdict: Literal["pass", "revise", "needs_manual"]
    reservation_ids: tuple[Identifier, ...] = Field(max_length=20)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=30)
    reason_codes: tuple[Literal[
        "constraints_satisfied", "insufficient_evidence", "candidate_mismatch",
        "explanation_incomplete", "manual_review",
    ], ...] = Field(min_length=1, max_length=5)
    summary: str = Field(min_length=1, max_length=800)
    tools: tuple[ToolRequest, ...] = Field(max_length=6)

    @model_validator(mode="after")
    def consistent_verdict(self):
        if self.verdict == "pass" and set(self.reason_codes) != {"constraints_satisfied"}:
            raise ValueError("Passing verification requires consistent reason codes")
        return self


_TOOL_PURPOSES = {
    "get_incident": "Read this run's bound incident; no incident_id argument is accepted.",
    "get_impacted_reservations": "Read the full impact set for this run's bound incident.",
    "get_booking_rules": "Read the current run's booking rules.",
    "list_equipment_candidates": "Read candidates for one supplied affected reservation; copy its exact ID.",
    "validate_candidate_plan": "Recheck only the chosen offered/presented plan; changes are bound by the orchestrator.",
    "append_run_step": "Request a factual audit marker only; no custom contents, status or call counts.",
}


def tool_contracts(allowed_tools: tuple[str, ...], reservation_ids: tuple[str, ...]) -> dict:
    return {name: {
        "purpose": _TOOL_PURPOSES[name],
        "arguments": {"reservation_id": {"type": "string", "enum": list(reservation_ids)}
                      if name == "list_equipment_candidates" else {"type": "null"}},
    } for name in sorted(allowed_tools)
       if name != "list_equipment_candidates" or reservation_ids}


def role_response_schema(response_type: type[BaseModel], contracts: dict, evidence: dict | None = None) -> dict:
    """Narrow the wire schema; runtime permissions remain the final authority."""
    schema = deepcopy(response_type.model_json_schema())
    definition = schema["$defs"]["ToolRequest"]
    branches = []
    ordinary = sorted(name for name in contracts if name != "list_equipment_candidates")
    if ordinary:
        branch = deepcopy(definition)
        branch["properties"]["name"]["enum"] = ordinary
        branch["properties"]["reservation_id"] = {"type": "null"}
        branches.append(branch)
    if "list_equipment_candidates" in contracts:
        branch = deepcopy(definition)
        branch["properties"]["name"]["enum"] = ["list_equipment_candidates"]
        identifier = next(item for item in definition["properties"]["reservation_id"]["anyOf"]
                          if item.get("type") == "string")
        branch["properties"]["reservation_id"] = {
            **identifier, **contracts["list_equipment_candidates"]["arguments"]["reservation_id"],
        }
        branches.append(branch)
    schema["$defs"]["ToolRequest"] = branches[0] if len(branches) == 1 else {"anyOf": branches}
    if evidence and "reservation_ids" in schema["properties"]:
        identifiers = evidence["required_reservation_ids"]
        reservations = schema["properties"]["reservation_ids"]
        reservations["items"]["enum"] = identifiers
        reservations["minItems"] = reservations["maxItems"] = len(identifiers)
        schema["properties"]["evidence_ids"]["items"] = {
            "type": "string", "enum": evidence["allowed_evidence_ids"],
        }
    return schema
