"""Real Main SQLite + Core graph with explicitly offline model responses."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json

import pytest
from pydantic import ValidationError

from campusops.bootstrap import create_application
from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import (
    ReservationCreate, ReservationCancelCreate, ReservationChangeCreate, ModelResponse,
)
from .test_integrated_runtime import OfflineModelClient, integrated


def create_command(service, **patch):
    item = next(row for row in service.list_equipment() if row.id == "cam-01")
    return ReservationCreate(**{**dict(equipment_id=item.id, expected_equipment_version=item.version,
        borrower_alias="匿名测试借用者", starts_at="2026-11-14T09:00:00+08:00", ends_at="2026-11-14T10:00:00+08:00",
        required_capabilities=("photo",), actor_alias="管理员", note="虚构测试", idempotency_key="create-test"), **patch})


def edit_command(service, **patch):
    item = service.list_reservations()[0]
    return ReservationChangeCreate(**{**dict(reservation_id=item.id, expected_reservation_version=item.version,
        starts_at="2026-11-14T11:00:00+08:00", ends_at="2026-11-14T12:00:00+08:00",
        requested_equipment_id="cam-02", borrower_alias=item.borrower_alias, required_capabilities=("photo",),
        actor_alias="管理员", reason="虚构修改时间和器材", idempotency_key="edit-test"), **patch})


def cancel_command(service, row=None, **patch):
    row = row or service.list_reservations()[0]
    return ReservationCancelCreate(**{**dict(reservation_id=row.id, expected_reservation_version=row.version,
        actor_alias="管理员", reason="虚构取消原因", idempotency_key="cancel-test"), **patch})


def test_create_readback_idempotency_and_original_records_untouched(service, database):
    original = service.list_reservations()
    command = create_command(service)
    result = service.create_reservation(command)
    assert len(service.list_reservations()) == 21 and result.reservation.status == "ACTIVE"
    assert result.reservation.starts_at.hour == 1 and result.reservation.version == 1
    assert create_application(database.path).create_reservation(command) == result
    assert all(row in service.list_reservations() for row in original)
    assert service.find_reservation_command(command.idempotency_key) == result
    assert not service.list_runs() and not service.list_incidents()
    with pytest.raises(CampusOpsError) as exc:
        service.create_reservation(command.model_copy(update={"borrower_alias": "不同借用者"}))
    assert exc.value.error.code == "IDEMPOTENCY_CONFLICT"


def test_concurrent_overlapping_create_can_only_reserve_once(service):
    barrier = Barrier(2)
    def reserve(key):
        barrier.wait()
        try:return service.create_reservation(create_command(service, idempotency_key=key)).reservation.id
        except CampusOpsError as exc:return exc.error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ("a", "b")))
    assert results.count("RESERVATION_OVERLAP") == 1
    assert len(service.list_reservations()) == 21


def test_create_half_open_adjacent_is_allowed_overlap_and_capability_rejected(service):
    service.create_reservation(create_command(service))
    service.create_reservation(create_command(service, starts_at="2026-11-14T10:00:00+08:00", ends_at="2026-11-14T11:00:00+08:00", idempotency_key="adjacent"))
    with pytest.raises(CampusOpsError) as exc:
        service.create_reservation(create_command(service, starts_at="2026-11-14T09:59:00+08:00", idempotency_key="overlap"))
    assert exc.value.error.code == "RESERVATION_OVERLAP"
    with pytest.raises(CampusOpsError) as exc:
        service.create_reservation(create_command(service, required_capabilities=("lighting",), idempotency_key="wrong-capability"))
    assert exc.value.error.code == "CAPABILITY_MISMATCH"


def test_faulted_equipment_and_stale_preview_rejected(service, fault):
    with pytest.raises(CampusOpsError) as exc:
        service.create_reservation(create_command(service))
    assert exc.value.error.code == "RESOURCE_UNAVAILABLE"
    with pytest.raises(CampusOpsError) as exc:
        service.create_reservation(create_command(service, expected_equipment_version=1))
    assert exc.value.error.code == "SNAPSHOT_CONFLICT"


@pytest.mark.parametrize("patch", [dict(starts_at="2026-11-14T11:00:00+08:00"), dict(ends_at="2026-11-14T08:00:00+08:00"),
    dict(starts_at="2026-11-14T09:00:00"), dict(borrower_alias="   "), dict(required_capabilities=())])
def test_invalid_new_booking_never_reaches_database(service, patch):
    with pytest.raises(ValidationError):create_command(service, **patch)
    assert len(service.list_reservations()) == 20


def test_cancel_preserves_history_releases_slot_and_survives_restart(service, database):
    result = service.create_reservation(create_command(service))
    command = cancel_command(service, result.reservation)
    cancelled = service.cancel_reservation(command)
    assert cancelled.reservation.id == result.reservation.id and cancelled.reservation.status == "CANCELLED"
    assert cancelled.reservation.version == result.reservation.version + 1
    assert len(service.list_reservations()) == 21
    assert create_application(database.path).cancel_reservation(command) == cancelled
    service.create_reservation(create_command(service, idempotency_key="same-slot-after-cancel"))
    assert len(service.list_reservations()) == 22


def test_cancel_rolls_back_if_audit_write_fails(service, database):
    original = service.list_reservations()
    with database.transaction() as connection:
        connection.execute("CREATE TRIGGER cancel_fail BEFORE INSERT ON audit_log WHEN NEW.action='RESERVATION_CANCELLED' BEGIN SELECT RAISE(ABORT,'TEST_INJECTED'); END")
    with pytest.raises(CampusOpsError) as exc:service.cancel_reservation(cancel_command(service))
    assert exc.value.error.code == "DATABASE_ERROR" and service.list_reservations() == original


def test_edit_calls_real_core_three_roles_before_approval_keeps_id(database):
    client = OfflineModelClient()
    service = integrated(database, client)
    before = service.list_reservations()
    request = edit_command(service)
    run = service.plan_reservation_change(request)
    assert run.status == "WAIT_APPROVAL", run
    assert len(client.requests) == run.model_calls == 3
    assert service.list_reservations() == before and not service.list_incidents()
    assert run.context.kind == "reservation_change"
    result = service.approve_reservation_change(run.id, "审批员", "approve-edit")
    assert result.status == "APPLIED" and result.reservation.id == request.reservation_id
    assert len(service.list_reservations()) == 20
    assert result.reservation.equipment_id == "cam-02" and result.reservation.starts_at == request.starts_at
    assert result.reservation.version == run.context.before.version + 1
    assert service.approve_reservation_change(run.id, "审批员", "approve-edit") == result
    assert create_application(database.path).get_reservation_change(run.id).decision == result
    assert len(client.requests) == 3


def test_edit_duplicate_start_reuses_result_and_a_second_active_request_is_not_dispatched(database):
    client = OfflineModelClient();service = integrated(database, client)
    request = edit_command(service)
    first = service.plan_reservation_change(request)
    assert service.plan_reservation_change(request) == first
    with pytest.raises(CampusOpsError):
        service.plan_reservation_change(request.model_copy(update={"idempotency_key": "other", "borrower_alias": "其他匿名借用者"}))
    assert len(client.requests) == 3


def test_cancel_after_edit_plan_makes_approval_stale(database):
    client = OfflineModelClient();service = integrated(database, client)
    run = service.plan_reservation_change(edit_command(service))
    command = cancel_command(service)
    service.cancel_reservation(command)
    before = service.list_reservations()
    decision = service.approve_reservation_change(run.id, "审批员", "stale-edit")
    assert decision.status == "STALE" and service.list_reservations() == before
    assert len(client.requests) == 3


def test_edit_reject_does_not_write_original_or_call_model_again(database):
    client = OfflineModelClient();service = integrated(database, client)
    before = service.list_reservations()
    run = service.plan_reservation_change(edit_command(service))
    result = service.reject_reservation_change(run.id, "审批员", "保留原安排")
    assert result.status == "REJECTED" and service.list_reservations() == before
    assert len(client.requests) == 3


def test_edit_disabled_agent_zero_calls_and_no_available_offer_goes_manual(database):
    from campusops.contracts.models import AgentConfigPatch
    client = OfflineModelClient();service = integrated(database, client)
    service.update_agent_config("planner-agent", AgentConfigPatch(enabled=False))
    assert service.plan_reservation_change(edit_command(service)).status == "NEEDS_MANUAL"
    assert not client.requests
    service.update_agent_config("planner-agent", AgentConfigPatch(enabled=True))
    run = service.plan_reservation_change(edit_command(service, idempotency_key="no-candidate", requested_equipment_id="light-01"))
    assert run.status == "NEEDS_MANUAL" and len(client.requests) == 1


def test_edit_replanning_is_bounded_and_all_attempts_counted(database):
    client = OfflineModelClient(revisions=5);service = integrated(database, client, calls=4)
    original = service.list_reservations()
    run = service.plan_reservation_change(edit_command(service))
    assert run.status == "FAILED" and run.error.code == "BUDGET_EXCEEDED"
    assert len(client.requests) == run.model_calls == 4 and run.retry_count <= 2
    assert service.list_reservations() == original


def test_edit_extra_observation_reaches_same_role_and_changes_final_evidence(database):
    class ObservationClient(OfflineModelClient):
        def complete(self, request):
            payload = json.loads(request.messages[1].content)
            role = json.loads(request.response_schema)["title"]
            if role == "ImpactReport":
                self.requests.append(request)
                if len(request.messages) == 2:
                    result = {"reservation_ids": [payload["reservations"][0]["id"]], "evidence_ids": ["not-yet-observed"],
                              "summary": "OFFLINE provisional", "tools": [{"name": "get_incident", "reservation_id": None}]}
                else:
                    observation = json.loads(request.messages[-1].content)["tool_observations"][0]
                    assert observation["result"]["kind"] == "reservation_change"
                    assert observation["result"]["requested"]["starts_at"] == "2026-11-14T03:00:00+00:00"
                    assert observation["source"]["port"] == "ReservationChangeReadonlyPort"
                    result = {"reservation_ids": [observation["result"]["before"]["id"]],
                              "evidence_ids": ["snapshot:" + observation["result"]["snapshot_fingerprint"]],
                              "summary": "OFFLINE evidence corrected using actual observation", "tools": []}
                return ModelResponse(content=json.dumps(result), provider="TEST_INJECTED", model_id="offline_mock_test")
            return super().complete(request)
    client = ObservationClient();service = integrated(database, client)
    run = service.plan_reservation_change(edit_command(service))
    assert run.status == "WAIT_APPROVAL" and run.model_calls == len(client.requests) == 4
    assert client.requests[0].agent_id == client.requests[1].agent_id == "impact-agent"


def test_create_and_cancel_invalidate_pending_fault_plan_without_running_agent(offline_service, fault):
    plan = offline_service.start_workflow(fault.id)
    command = create_command(offline_service, equipment_id="cam-02", expected_equipment_version=1)
    result = offline_service.create_reservation(command)
    assert plan.proposal_id in result.invalidated_proposal_ids
    assert offline_service.get_proposal(plan.proposal_id).status == "STALE"


def test_new_booking_audit_failure_rolls_back_insert_and_pending_status(offline_service, fault, database):
    plan=offline_service.start_workflow(fault.id)
    before=offline_service.list_reservations()
    with database.transaction() as connection:
        connection.execute("CREATE TRIGGER create_fail BEFORE INSERT ON audit_log WHEN NEW.action='RESERVATION_CREATED' BEGIN SELECT RAISE(ABORT,'TEST_INJECTED'); END")
    with pytest.raises(CampusOpsError):
        offline_service.create_reservation(create_command(offline_service,equipment_id="cam-02",expected_equipment_version=1))
    assert offline_service.list_reservations()==before
    assert offline_service.get_proposal(plan.proposal_id).status=="VALIDATED"


def test_change_approval_failure_rolls_back_original_and_success_audit(database):
    client=OfflineModelClient();service=integrated(database,client)
    before=service.list_reservations()
    run=service.plan_reservation_change(edit_command(service))
    with database.transaction() as connection:
        connection.execute("CREATE TRIGGER edit_fail BEFORE INSERT ON audit_log WHEN NEW.action='RESERVATION_CHANGE_DECIDED' BEGIN SELECT RAISE(ABORT,'TEST_INJECTED'); END")
    with pytest.raises(CampusOpsError):service.approve_reservation_change(run.id,"审批员","rollback-edit")
    assert service.list_reservations()==before and service.get_reservation_change(run.id).status=="WAIT_APPROVAL"
    assert service.get_reservation_change(run.id).decision is None


def test_concurrent_repeated_edit_approval_commits_once(database):
    client=OfflineModelClient();service=integrated(database,client)
    run=service.plan_reservation_change(edit_command(service))
    barrier=Barrier(2)
    def approve(_):
        barrier.wait();return service.approve_reservation_change(run.id,"审批员","same-edit")
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(approve,range(2)))
    assert results[0]==results[1] and results[0].reservation.version==run.context.before.version+1
    assert len(service.list_reservations())==20 and len(client.requests)==3


def test_role_prompts_are_task_specific_and_never_send_alias_or_free_reason(database):
    from campusops.agent_core.reservation_change import resolve_change_prompt
    from campusops.agent_core.prompts import resolve_prompt
    client=OfflineModelClient();service=integrated(database,client)
    request=edit_command(service,actor_alias="FICTIONAL_PRIVATE_ACTOR",reason="FICTIONAL_PRIVATE_REASON",borrower_alias="FICTIONAL_PRIVATE_BORROWER")
    assert service.plan_reservation_change(request).status=="WAIT_APPROVAL"
    for role in ("impact","planner","verifier"):
        assert resolve_change_prompt(role,"v1").body_sha256!=resolve_change_prompt(role,"v2").body_sha256
        assert resolve_change_prompt(role,"v1").body_sha256!=resolve_prompt(role,"v1").body_sha256
    payload=json.dumps([r.model_dump(mode="json") for r in client.requests])
    assert not any(value in payload for value in (request.actor_alias,request.reason,request.borrower_alias))
