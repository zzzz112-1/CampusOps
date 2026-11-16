"""Fictional deterministic search, including deliberate invalid-data injections."""

import pytest

from campusops.agent_core import CoreSettings
from campusops.agent_core.solver import overlaps, solve
from campusops.contracts.models import Incident
from .support import MemoryReadonly, equipment, reservation, NOW


def test_half_open_adjacency_allows_reusing_replacement():
    port = MemoryReadonly(3)
    result = solve(port.snapshot, port.impacted, port.candidates, CoreSettings())
    assert result.exhausted and len(result.plans) == 8
    assert len({item.to_equipment_id for item in result.plans[0].changes}) == 1
    assert result == solve(port.snapshot, tuple(reversed(port.impacted)),
                           dict(reversed(list(port.candidates.items()))), CoreSettings())


def test_internal_overlap_backtracks_to_globally_compatible_assignment():
    # FAILURE_INJECTION_TEST: an overlapping impact set tests the solver independently.
    port = MemoryReadonly(2)
    overlapping = (reservation("booking_0"), reservation("booking_1"))
    port.snapshot = port.snapshot.model_copy(update={"reservations": overlapping})
    result = solve(port.snapshot, overlapping, port.candidates, CoreSettings())
    assert result.exhausted and len(result.plans) == 2
    for plan in result.plans:
        assert len({item.to_equipment_id for item in plan.changes}) == 2
        assert overlaps(*plan.changes)
    one_candidate = {key: (port.equipment[1],) for key in port.candidates}
    impossible = solve(port.snapshot, overlapping, one_candidate, CoreSettings())
    assert impossible.exhausted and not impossible.plans


@pytest.mark.parametrize("patch", [
    {"capabilities": ()}, {"status": "FAULTED"}, {"status": "MAINTENANCE"},
])
def test_untrusted_candidate_list_cannot_bypass_capabilities_or_state(patch):
    port = MemoryReadonly()
    candidate = equipment("camera_a", **patch)
    snapshot = port.snapshot.model_copy(update={"equipment": (port.equipment[0], candidate)})
    result = solve(snapshot, port.impacted, {port.impacted[0].id: (candidate,)}, CoreSettings())
    assert not result.plans and result.exhausted


def test_existing_booking_and_open_incident_block_candidates():
    port = MemoryReadonly()
    existing = reservation("unaffected", equipment_id="camera_a")
    incident = Incident(id="other_incident", equipment_id="camera_b", effective_at=NOW,
                        reason="Fictional blocked device", created_at=NOW)
    snapshot = port.snapshot.model_copy(update={"reservations": port.impacted + (existing,),
                                                "incidents": port.snapshot.incidents + (incident,)})
    assert not solve(snapshot, port.impacted, port.candidates, CoreSettings()).plans


def test_invented_or_changed_candidate_and_original_equipment_are_excluded():
    port = MemoryReadonly()
    choices = (equipment("invented"), equipment("camera_a", version=2), port.equipment[0])
    result = solve(port.snapshot, port.impacted, {port.impacted[0].id: choices}, CoreSettings())
    assert not result.plans


def test_time_and_reservation_version_are_never_rewritten():
    port = MemoryReadonly(3)
    result = solve(port.snapshot, port.impacted, port.candidates, CoreSettings())
    originals = {row.id: row for row in port.impacted}
    for plan in result.plans:
        assert len(plan.changes) == len(originals)
        for item in plan.changes:
            original = originals[item.reservation_id]
            assert (item.starts_at, item.ends_at, item.expected_reservation_version) == (
                original.starts_at, original.ends_at, original.version)


def test_soft_rank_prefers_category_then_capability_excess_then_id():
    port = MemoryReadonly()
    choices = (equipment("a_other_category", category="other"),
               equipment("b_extra_capability", capabilities=("photo", "video")),
               equipment("c_exact"), equipment("d_exact"))
    snapshot = port.snapshot.model_copy(update={"equipment": (port.equipment[0],) + choices})
    result = solve(snapshot, port.impacted, {port.impacted[0].id: choices}, CoreSettings())
    assert [row.changes[0].to_equipment_id for row in result.plans] == [
        "c_exact", "d_exact", "b_extra_capability", "a_other_category"]


def test_search_limit_is_bounded_and_does_not_claim_global_optimality():
    port = MemoryReadonly(3)
    result = solve(port.snapshot, port.impacted, port.candidates, CoreSettings(search_nodes=4))
    assert result.visited == 4 and not result.exhausted and len(result.plans) == 1


def test_fault_effective_time_respects_half_open_boundary():
    port = MemoryReadonly()
    booking = port.impacted[0]
    incident = Incident(id="other_incident", equipment_id="camera_a", effective_at=booking.ends_at,
                        reason="Fictional future fault", created_at=NOW)
    snapshot = port.snapshot.model_copy(update={"incidents": port.snapshot.incidents + (incident,)})
    result = solve(snapshot, port.impacted, {booking.id: (port.equipment[1],)}, CoreSettings())
    assert result.plans
