"""Bounded, deterministic multi-booking search; Main remains final validator."""

from dataclasses import dataclass

from campusops.agent_core.config import CoreSettings
from campusops.contracts.models import (
    Equipment, EquipmentStatus, ProposalItem, Reservation, ReservationStatus, ResourceSnapshot,
)


@dataclass(frozen=True)
class OfferedPlan:
    plan_id: str
    changes: tuple[ProposalItem, ...]
    score: tuple


@dataclass(frozen=True)
class SearchResult:
    plans: tuple[OfferedPlan, ...]
    visited: int
    exhausted: bool


def overlaps(left, right) -> bool:
    return left.starts_at < right.ends_at and right.starts_at < left.ends_at


def solve(snapshot: ResourceSnapshot, impacted: tuple[Reservation, ...],
          candidates: dict[str, tuple[Equipment, ...]], settings: CoreSettings) -> SearchResult:
    impacted_ids = {row.id for row in impacted}
    equipment = {row.id: row for row in snapshot.equipment}
    options = {}
    for booking in impacted:
        allowed = []
        for candidate in candidates.get(booking.id, ()):
            actual = equipment.get(candidate.id)
            if (actual != candidate or actual.id == booking.equipment_id
                    or actual.status != EquipmentStatus.AVAILABLE
                    or not set(booking.required_capabilities) <= set(actual.capabilities)):
                continue
            if any(incident.status == "OPEN" and incident.equipment_id == actual.id
                   and booking.ends_at > incident.effective_at for incident in snapshot.incidents):
                continue
            if any(other.status == ReservationStatus.ACTIVE and other.id not in impacted_ids
                   and other.equipment_id == actual.id and overlaps(booking, other)
                   for other in snapshot.reservations):
                continue
            allowed.append(actual)
        options[booking.id] = tuple(sorted({row.id: row for row in allowed}.values(), key=lambda row: row.id))
    order = sorted(impacted, key=lambda row: (len(options[row.id]), row.id))
    found: list[tuple[tuple, tuple[ProposalItem, ...]]] = []
    visited = 0
    exhausted = True

    def search(index: int, changes: tuple[ProposalItem, ...]):
        nonlocal visited, exhausted
        if visited >= settings.search_nodes:
            exhausted = False
            return
        visited += 1
        if index == len(order):
            ordered = tuple(sorted(changes, key=lambda row: row.reservation_id))
            category_changes = sum(equipment[item.from_equipment_id].category != equipment[item.to_equipment_id].category
                                   for item in ordered)
            capability_excess = sum(len(set(equipment[item.to_equipment_id].capabilities)
                                        - set(next(row for row in impacted if row.id == item.reservation_id).required_capabilities))
                                    for item in ordered)
            score = (category_changes, capability_excess, tuple(item.to_equipment_id for item in ordered))
            found.append((score, ordered))
            found.sort(key=lambda row: row[0])
            del found[settings.offered_plans:]
            return
        booking = order[index]
        for candidate in options[booking.id]:
            if any(item.to_equipment_id == candidate.id and overlaps(booking, item) for item in changes):
                continue
            item = ProposalItem(reservation_id=booking.id, from_equipment_id=booking.equipment_id,
                                to_equipment_id=candidate.id, starts_at=booking.starts_at,
                                ends_at=booking.ends_at, expected_reservation_version=booking.version)
            search(index + 1, changes + (item,))
            if not exhausted:
                break

    search(0, ())
    return SearchResult(tuple(OfferedPlan(f"plan_{index}", changes, score)
                              for index, (score, changes) in enumerate(found)), visited, exhausted)
