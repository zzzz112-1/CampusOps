"""Deterministic constraints. No storage, network or LLM dependency."""

from itertools import combinations

from campusops.contracts.models import (
    Equipment, EquipmentStatus, ErrorCode, Incident, ProposalItem, Reservation,
    ReservationStatus, ResourceSnapshot, ValidationIssue, ValidationResult,
)


def overlaps(first, second) -> bool:
    return first.starts_at < second.ends_at and second.starts_at < first.ends_at


def impacted_reservations(snapshot: ResourceSnapshot, incident: Incident) -> tuple[Reservation, ...]:
    return tuple(
        item for item in snapshot.reservations
        if item.status == ReservationStatus.ACTIVE
        and item.equipment_id == incident.equipment_id
        and item.ends_at > incident.effective_at
    )


def available_for(snapshot: ResourceSnapshot, equipment: Equipment, booking: Reservation) -> bool:
    return (
        equipment.status == EquipmentStatus.AVAILABLE
        and set(booking.required_capabilities).issubset(equipment.capabilities)
        and not any(
            incident.status == "OPEN" and incident.equipment_id == equipment.id
            and booking.ends_at > incident.effective_at
            for incident in snapshot.incidents
        )
    )


def candidates(snapshot: ResourceSnapshot, booking: Reservation) -> tuple[Equipment, ...]:
    """Single-booking availability only; a combined plan still needs validation."""
    return tuple(
        equipment for equipment in snapshot.equipment
        if equipment.id != booking.equipment_id
        and available_for(snapshot, equipment, booking)
        and not any(
            other.id != booking.id and other.status == ReservationStatus.ACTIVE
            and other.equipment_id == equipment.id and overlaps(booking, other)
            for other in snapshot.reservations
        )
    )


def validate_plan(
    snapshot: ResourceSnapshot, incident_id: str, changes: tuple[ProposalItem, ...],
) -> ValidationResult:
    violations: list[ValidationIssue] = []

    def issue(code: ErrorCode, message: str, entity_id: str | None = None):
        violations.append(ValidationIssue(code=code, message=message, entity_id=entity_id))

    incident = next((row for row in snapshot.incidents if row.id == incident_id), None)
    if not incident or incident.status != "OPEN":
        issue(ErrorCode.INVALID_STATE, "Incident is missing or no longer open", incident_id)
        impacted = ()
    else:
        impacted = impacted_reservations(snapshot, incident)
    expected_ids = {row.id for row in impacted}
    change_ids = [row.reservation_id for row in changes]
    if len(change_ids) != len(set(change_ids)):
        issue(ErrorCode.INVALID_PROPOSAL, "A reservation appears more than once")
    if set(change_ids) != expected_ids:
        issue(ErrorCode.INCOMPLETE_PLAN, "Changes must cover exactly the incident's impacted active bookings")

    bookings = {row.id: row for row in snapshot.reservations}
    resources = {row.id: row for row in snapshot.equipment}
    reassigned: dict[str, str] = {}
    for change in changes:
        booking = bookings.get(change.reservation_id)
        equipment = resources.get(change.to_equipment_id)
        if not booking:
            issue(ErrorCode.NOT_FOUND, "Reservation does not exist", change.reservation_id)
            continue
        if booking.status != ReservationStatus.ACTIVE:
            issue(ErrorCode.INVALID_PROPOSAL, "Only active bookings can be reassigned", booking.id)
        if booking.equipment_id != change.from_equipment_id:
            issue(ErrorCode.SNAPSHOT_CONFLICT, "Original equipment differs from the proposal", booking.id)
        if booking.version != change.expected_reservation_version:
            issue(ErrorCode.SNAPSHOT_CONFLICT, "Reservation version differs from the proposal", booking.id)
        if booking.starts_at != change.starts_at or booking.ends_at != change.ends_at:
            issue(ErrorCode.INVALID_PROPOSAL, "Rescheduling time is outside P0; original times must be preserved", booking.id)
        if change.to_equipment_id == change.from_equipment_id:
            issue(ErrorCode.INVALID_PROPOSAL, "Replacement equipment must differ", booking.id)
        if not equipment:
            issue(ErrorCode.NOT_FOUND, "Replacement equipment does not exist", change.to_equipment_id)
        else:
            if not set(booking.required_capabilities).issubset(equipment.capabilities):
                issue(ErrorCode.CAPABILITY_MISMATCH, "Replacement lacks required capabilities", booking.id)
            if equipment.status != EquipmentStatus.AVAILABLE or any(
                row.status == "OPEN" and row.equipment_id == equipment.id
                and booking.ends_at > row.effective_at for row in snapshot.incidents
            ):
                issue(ErrorCode.RESOURCE_UNAVAILABLE, "Replacement is unavailable or incident-blocked", equipment.id)
        reassigned[booking.id] = change.to_equipment_id

    active = [row for row in snapshot.reservations if row.status == ReservationStatus.ACTIVE]
    for first, second in combinations(active, 2):
        if (
            reassigned.get(first.id, first.equipment_id) == reassigned.get(second.id, second.equipment_id)
            and overlaps(first, second)
        ):
            issue(ErrorCode.RESERVATION_OVERLAP, f"Bookings {first.id} and {second.id} overlap", first.id)

    return ValidationResult(
        passed=not violations, issues=tuple(row.message for row in violations),
        violations=tuple(violations),
        evidence_ids=(f"snapshot:{snapshot.fingerprint}", f"incident:{incident_id}")
        + tuple(f"reservation:{row.id}:v{row.version}" for row in impacted),
    )
