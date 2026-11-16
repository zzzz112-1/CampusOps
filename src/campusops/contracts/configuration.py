"""Main-published configuration limits; no role gains a database write tool."""

from types import MappingProxyType

SUPPORTED_PROMPT_VERSIONS = ("v1", "v2")
_IMPACT = frozenset({"get_incident", "get_impacted_reservations", "get_booking_rules", "append_run_step"})
_PLANNING = _IMPACT | {"list_equipment_candidates", "validate_candidate_plan"}
ROLE_TOOL_LIMITS = MappingProxyType({"impact": _IMPACT, "planner": _PLANNING, "verifier": _PLANNING})
