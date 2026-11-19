"""Exercises the demo application and its explicit model configuration."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import socket

import pytest

from campusops.bootstrap import create_application
from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import (EquipmentCreate, ExecutionMode, IncidentCreate,
    ReservationCancelCreate, ReservationCreate)
from campusops.reviewer import initialize_data, configure_environment
from campusops.security.service import protect_service
from .test_integrated_runtime import OfflineModelClient
from campusops.agent_core import CoreSettings, GearShiftOrchestrator

ROOT = Path(__file__).resolve().parents[2]
_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex


def normal_service(context, client=None):
    initialize_data(context)
    service = create_application(context.database, seed_asset=context.seed,
        orchestration=GearShiftOrchestrator(CoreSettings()), model_client=client,
        execution_mode=ExecutionMode.OFFLINE_MOCK_TEST if client else ExecutionMode.REAL_API,
        provider="TEST_INJECTED" if client else None, model_id="offline-review-test" if client else None)
    return protect_service(service, context)


def reservation_request(service, **patch):
    equipment = next(row for row in service.list_equipment() if row.id == "cam-01")
    return ReservationCreate(**{**dict(equipment_id="cam-01", expected_equipment_version=equipment.version,
        borrower_alias="匿名评审借用者", starts_at="2026-11-14T09:00:00+08:00",
        ends_at="2026-11-14T10:00:00+08:00", required_capabilities=("photo",),
        actor_alias="评审员", note="虚构评审测试", idempotency_key="review-create"), **patch})


def test_first_init_and_second_start_preserve_data(review_context):
    service = normal_service(review_context)
    assert len(service.list_equipment()) == 10 and len(service.list_reservations()) == 20
    created = service.create_reservation(reservation_request(service)).reservation
    before = service.list_reservations()
    restarted = normal_service(review_context)
    assert restarted.list_reservations() == before
    assert any(row.id == created.id for row in restarted.list_reservations())


def test_unknown_database_is_preserved_and_rejected(review_context):
    review_context.database.parent.mkdir(parents=True)
    review_context.database.write_bytes(b"unknown data retained")
    with pytest.raises(CampusOpsError):
        initialize_data(review_context)
    assert review_context.database.read_bytes() == b"unknown data retained"


def test_interrupted_initialization_is_not_repaired(review_context):
    review_context.directory.mkdir(parents=True)
    marker = {"application": "campusops-review-unlocked", "format": 1,
              "root": str(review_context.root), "instance_id": "a" * 32, "ready": False}
    path = review_context.directory / "instance.json"
    path.write_text(json.dumps(marker))
    before = path.read_bytes()
    with pytest.raises(CampusOpsError):
        initialize_data(review_context)
    assert path.read_bytes() == before and not review_context.database.exists()


def test_original_paths_and_model_opt_in_are_ignored(review_context, monkeypatch):
    initialize_data(review_context)
    for name, value in {"DATABASE_PATH": "D:/CampusOps/var/data/campusops.db",
        "CAMPUSOPS_BUDGET_PROJECT_ROOT": "D:/CampusOps", "CAMPUSOPS_LLM_ENABLED": "true",
        "MODEL_API_KEY": "fictional-old-env-ignored", "MODEL_BASE_URL": "https://fictional.example/v1",
        "MODEL_ID": "old-model"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CAMPUSOPS_REVIEW_LLM_ENABLED", raising=False)
    configure_environment(review_context)
    assert os.environ["DATABASE_PATH"] == str(review_context.database)
    assert os.environ["CAMPUSOPS_LLM_ENABLED"] == "false"
    assert "MODEL_API_KEY" not in os.environ and "CAMPUSOPS_BUDGET_PROJECT_ROOT" not in os.environ


def test_deterministic_create_cancel_and_catalog(review_context):
    service = normal_service(review_context)
    created = service.create_reservation(reservation_request(service)).reservation
    result = service.cancel_reservation(ReservationCancelCreate(reservation_id=created.id,
        expected_reservation_version=created.version, actor_alias="评审员", reason="虚构取消",
        idempotency_key="review-cancel"))
    assert result.reservation.status == "CANCELLED"
    service.create_equipment(EquipmentCreate(name="评审测试相机", category="camera", capabilities=("photo",),
        actor_alias="评审员", note="独立虚构台账", idempotency_key="review-equipment"))
    assert len(service.list_equipment()) == 11


def test_conflicting_reservation_is_rejected(review_context):
    service = normal_service(review_context)
    service.create_reservation(reservation_request(service))
    before = service.list_reservations()
    with pytest.raises(CampusOpsError):
        service.create_reservation(reservation_request(service, idempotency_key="review-conflict"))
    assert service.list_reservations() == before


def test_unconfigured_generation_is_explicit_with_zero_calls(review_context):
    service = normal_service(review_context)
    incident = service.register_equipment_failure(IncidentCreate(equipment_id="cam-01",
        effective_at="2026-11-10T08:00:00+08:00", reason="虚构无模型测试"))
    before = service.list_reservations()
    result = service.start_workflow(incident.id)
    assert result.status == "FAILED" and result.error.code == "PROVIDER_UNAVAILABLE"
    assert service.get_run(result.run_id).model_calls == 0
    assert service.list_reservations() == before


def test_injected_three_role_approval_and_readback(review_context):
    client = OfflineModelClient()
    service = normal_service(review_context, client)
    before = service.list_reservations()
    incident = service.register_equipment_failure(IncidentCreate(equipment_id="cam-01",
        effective_at="2026-11-10T08:00:00+08:00", reason="TEST_INJECTED 虚构审批链"))
    result = service.start_workflow(incident.id)
    assert result.status == "WAIT_APPROVAL" and len(client.requests) == 3
    assert service.list_reservations() == before
    applied = service.approve_proposal(result.proposal_id, "评审员", "review-approve")
    assert applied.status == "APPLIED"
    after = normal_service(review_context).list_reservations()
    changed = {row.id for row, old in zip(after, before) if row != old}
    assert changed == {"booking-01", "booking-02"}
    for row, old in zip(after, before):
        assert (row.starts_at, row.ends_at) == (old.starts_at, old.ends_at)
    assert service.approve_proposal(result.proposal_id, "评审员", "review-approve") == applied


def test_change_before_approval_invalidates_proposal(review_context):
    service = normal_service(review_context, OfflineModelClient())
    incident = service.register_equipment_failure(IncidentCreate(equipment_id="cam-01",
        effective_at="2026-11-10T08:00:00+08:00", reason="TEST_INJECTED 旧方案失效"))
    result = service.start_workflow(incident.id)
    service.create_equipment(EquipmentCreate(name="评审新增相机", category="camera", capabilities=("photo",),
        actor_alias="评审员", idempotency_key="review-bump-snapshot"))
    before = service.list_reservations()
    outcome = service.approve_proposal(result.proposal_id, "评审员", "review-stale")
    assert outcome.status == "STALE" and service.list_reservations() == before


def test_cli_actual_new_source_copy_no_owner_or_env(review_context):
    shutil.copytree(ROOT / "src", review_context.root / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    # Keep the copied source first while retaining runner-supplied dependency paths.
    search_paths = [str(review_context.root / "src")]
    if os.environ.get("PYTHONPATH"):
        search_paths.append(os.environ["PYTHONPATH"])
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(search_paths), CAMPUSOPS_REVIEW_LLM_ENABLED="false")
    for command in ("init-db", "seed-demo", "status"):
        result = subprocess.run([sys.executable, "-m", "campusops.cli", command],
            cwd=review_context.root, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Equipment=10; Reservations=20" in result.stdout
    assert not (review_context.root / ".env").exists()
    assert not (review_context.root / "maintenance").exists()


def test_actual_streamlit_entry_navigation_refresh_and_new_window(configured_context, monkeypatch):
    from streamlit.testing.v1 import AppTest
    # Windows asyncio uses a real loopback socketpair even for in-process AppTest.
    # Permit that local mechanism while continuing to reject every external host.
    def local_only(target):
        def connect(sock, address):
            assert isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")
            previous = sock.gettimeout()
            try:
                sock.settimeout(5)
                return target(sock, address)
            finally:
                sock.settimeout(previous)
        return connect
    monkeypatch.setattr(socket.socket, "connect", local_only(_socket_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", local_only(_socket_connect_ex))
    # Real require_ui_access and real bootstrap run without any auth injection.
    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=40).run()
    assert not app.exception
    assert any(row.value == "CampusOps" for row in app.title)
    assert not any("验证码" in row.label for row in app.text_input)
    for page in ("异常调度与审批", "处理记录与设置", "资源与预约"):
        app.radio(key="ui_page").set_value(page).run()
        assert not app.exception
    app.button(key="refresh").click().run()
    assert not app.exception
    app.button(key="reviewer_clear_operation").click().run()
    assert not app.exception
    second = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=40).run()
    assert not second.exception and any(row.value == "CampusOps" for row in second.title)


def test_explicit_model_composition_uses_independent_budget(configured_context, monkeypatch):
    from campusops.bootstrap import create_configured_application
    for name, value in {"CAMPUSOPS_REVIEW_LLM_ENABLED": "true",
        "CAMPUSOPS_REVIEW_MODEL_API_KEY": "fictional-review-composition-only",
        "CAMPUSOPS_REVIEW_MODEL_BASE_URL": "https://fictional.example/v1",
        "CAMPUSOPS_REVIEW_MODEL_ID": "fictional-review-model"}.items():
        monkeypatch.setenv(name, value)
    service = create_configured_application()
    assert service._model_client.budget.directory == configured_context.budget
    assert service._model_client.settings.limits.calls_per_run == 6
    assert not (configured_context.budget / "usage.jsonl").exists()
