"""Explicit projections of Main's readonly results for model observations."""

from campusops.contracts.models import Equipment, Incident, Reservation, ValidationResult


def booking_payload(row: Reservation) -> dict:
    return row.model_dump(mode="json", include={
        "id", "equipment_id", "starts_at", "ends_at", "required_capabilities", "version",
    })


def equipment_payload(row: Equipment) -> dict:
    return row.model_dump(mode="json", include={"id", "category", "capabilities", "status", "version"})


def incident_payload(row: Incident) -> dict:
    return row.model_dump(mode="json", include={"id", "equipment_id", "kind", "effective_at", "status"})


def validation_payload(result: ValidationResult) -> dict:
    # Main's typed codes and references are sufficient. Do not forward arbitrary
    # issue text or exception/error messages in a tool observation.
    return {
        "passed": result.passed,
        "evidence_ids": list(result.evidence_ids),
        "violations": [row.model_dump(mode="json", include={"code", "entity_id"})
                       for row in result.violations],
    }


def tool_result_payload(name: str, result):
    if name == "get_incident":
        return incident_payload(result)
    if name == "get_impacted_reservations":
        return [booking_payload(row) for row in result]
    if name == "list_equipment_candidates":
        return [equipment_payload(row) for row in result]
    if name == "get_booking_rules":
        # Main owns this trusted, fictional local rule material, including any
        # future compatible ID/version/body/source fields. Never load files here.
        return result.model_dump(mode="json")
    if name == "validate_candidate_plan":
        return validation_payload(result)
    if name == "append_run_step":
        return {"audit_marker_recorded": True, "run_id": result.run_id,
                "step_id": result.id, "seq": result.seq}
    raise ValueError("Unsupported readonly observation")
