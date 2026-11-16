"""Capability routing over the actual Main registry, with dependency evidence."""

from dataclasses import dataclass

from campusops.contracts.configuration import ROLE_TOOL_LIMITS
from campusops.contracts.models import AgentConfig


TOOLS = frozenset({
    "get_incident", "get_impacted_reservations", "list_equipment_candidates",
    "get_booking_rules", "validate_candidate_plan", "append_run_step",
})


@dataclass(frozen=True)
class Task:
    role: str
    skill: str
    required_tools: frozenset[str]
    dependencies: tuple[str, ...]
    priority: int


_READ = frozenset({"get_incident", "get_impacted_reservations", "get_booking_rules"})
TASKS = (
    Task("impact", "impact_analysis", _READ, (), 100),
    Task("planner", "scheduling", frozenset({"list_equipment_candidates", "validate_candidate_plan"}),
         ("impact",), 80),
    Task("verifier", "verification", _READ | {"list_equipment_candidates", "validate_candidate_plan"},
         ("planner",), 60),
)


@dataclass(frozen=True)
class Route:
    task: Task
    selected: AgentConfig | None
    eligible: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]


def select_agents(configs: tuple[AgentConfig, ...]) -> tuple[Route, ...]:
    routes = []
    for task in sorted(TASKS, key=lambda row: (-row.priority, row.role)):
        eligible = []
        rejected = []
        for agent in sorted(configs, key=lambda row: row.agent_id):
            if agent.role != task.role:
                continue
            reason = None
            if not agent.enabled:
                reason = "disabled"
            elif task.skill not in agent.skills:
                reason = "missing_skill"
            elif not task.required_tools <= set(agent.allowed_tools):
                reason = "missing_tool_permission"
            elif not set(agent.allowed_tools) <= ROLE_TOOL_LIMITS[task.role]:
                reason = "unsupported_tool_permission"
            if reason:
                rejected.append((agent.agent_id, reason))
            else:
                eligible.append(agent)
        routes.append(Route(task, eligible[0] if eligible else None,
                            tuple(agent.agent_id for agent in eligible), tuple(rejected)))
    return tuple(routes)
