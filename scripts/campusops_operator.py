"""Explicit CampusOps operator entry. Configuration checks never send a request.

No shell evaluation, dotenv interpolation, hidden retries, production mocks or
automatic provider switching. Secrets are read only into this process.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

from campusops.agent_core import CoreSettings, GearShiftOrchestrator, LLMAdapter
from campusops.agent_core.config import ModelSettings
from campusops.agent_core.errors import CoreFailure
from campusops.agent_core.roles import PlanningDecision
from campusops.agent_core.runtime import digest
from campusops.bootstrap import create_application
from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import ErrorCode, ExecutionMode, IncidentCreate, RunStepCreate
from campusops.storage.database import Database
from campusops.storage.seed import seed_demo

ROOT = Path(__file__).resolve().parents[1]
MODEL_KEYS = frozenset({
    "MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_ID", "MODEL_CALLS_PER_RUN_MAX",
    "MODEL_DAILY_CALLS_MAX", "REPLAN_MAX", "MODEL_TIMEOUT_SECONDS",
    "MODEL_MAX_OUTPUT_TOKENS", "MODEL_THINKING",
})
IGNORED_KEYS = frozenset({
    "DATABASE_PATH", "APP_TZ", "CAMPUSOPS_LLM_ENABLED", "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2",
})


class OperatorError(Exception):
    """Only fixed, non-secret messages are allowed here."""


def read_model_env(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise OperatorError("CONFIG_FILE_MISSING_OR_OUTSIDE_PROJECT")
    if path.stat().st_size > 65536:
        raise OperatorError("CONFIG_FILE_TOO_LARGE")
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise OperatorError("CONFIG_LINE_INVALID")
        key, value = (part.strip() for part in line.split("=", 1))
        if key not in MODEL_KEYS | IGNORED_KEYS or key in values:
            raise OperatorError("CONFIG_KEY_UNKNOWN_OR_DUPLICATED")
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise OperatorError("CONFIG_QUOTE_INVALID")
            value = value[1:-1]
        if "\x00" in value or "$(" in value or "${" in value or "`" in value:
            raise OperatorError("CONFIG_INTERPOLATION_NOT_SUPPORTED")
        # Enable flags, tracing and DB paths in the file cannot authorize actions.
        if key in MODEL_KEYS:
            values[key] = value
    return values


def configure(path: Path, *, enabled: bool, thinking: str | None = None) -> ModelSettings:
    values = read_model_env(path)
    os.environ.update(values)
    if thinking is not None:
        os.environ["MODEL_THINKING"] = thinking
    os.environ["CAMPUSOPS_LLM_ENABLED"] = "true" if enabled else "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    settings = ModelSettings.from_env()  # Validation only, never constructs a network request.
    if settings.limits.calls_per_run > 6 or settings.limits.daily_calls > 30:
        raise OperatorError("LIMIT_EXCEEDS_AUTHORIZED_6_PER_RUN_OR_30_DAILY")
    return settings


def require_live_authorization(args):
    if not args.account_protected or args.confirm != "REAL_API_FICTIONAL_APPROVAL":
        raise OperatorError("REAL_API_REQUIRES_ACCOUNT_PROTECTION_AND_EXPLICIT_CONFIRMATION")


def source_evidence() -> dict:
    files = sorted((ROOT / "src/campusops").rglob("*.py"))
    files += sorted((ROOT / "src/campusops/storage/migrations").glob("*.sql"))
    files += [ROOT / "streamlit_app.py", Path(__file__).resolve()]
    payload = "".join(f"{p.relative_to(ROOT).as_posix()}:{sha256(p.read_bytes()).hexdigest()}\n" for p in files)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    return {"git_head": head, "runtime_and_operator_sha256": sha256(payload.encode()).hexdigest()}


def prepare_database(directory: Path, scenario: str) -> Database:
    database = Database(directory / "fictional.db")
    if database.path.exists():
        raise OperatorError("ACCEPTANCE_DATABASE_MUST_BE_NEW")
    asset = ROOT / "assets/demo_seed.json"
    if scenario == "parallel":
        payload = json.loads(asset.read_text(encoding="utf-8"))
        payload["reservations"][4]["equipment_id"] = "cam-01"
        asset = directory / "fictional-three-booking-seed.json"
        with asset.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    database.migrate()
    seed_demo(database, asset)
    return database


class ObservedClient:
    """Count actual response usage without retaining content, headers or prompts."""

    def __init__(self, client):
        self.client = client
        self.responses = []

    def complete(self, request):
        response = self.client.complete(request)
        self.responses.append({
            "agent_id": request.agent_id, "provider": response.provider, "model_id": response.model_id,
            "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
        })
        return response

    def get_model_budget(self, run_id):
        return self.client.get_model_budget(run_id)


class OnePlanInjection:
    """Explicit acceptance-only transform; never a production model fallback."""

    marker = "FAILURE_INJECTION_UNOFFERED_PLAN"

    def __init__(self, client, port):
        self.client, self.port = client, port
        self.events, self.planner_inputs = [], []

    def get_model_budget(self, run_id):
        return self.client.get_model_budget(run_id)

    def complete(self, request):
        response = self.client.complete(request)
        if json.loads(request.response_schema)["title"] != "PlanningDecision":
            return response
        payload = json.loads(request.messages[1].content)
        self.planner_inputs.append({"retry_count": payload["retry_count"], "feedback": payload["feedback"]})
        if self.events:
            return response
        try:
            parsed = PlanningDecision.model_validate_json(response.content)
            offered = {row["plan_id"] for row in payload["offered_plans"]}
            if parsed.plan_id not in offered or self.marker in offered:
                raise ValueError("Injection needs an originally valid offered plan")
            affected = {row["id"] for row in payload["reservations"]}
            for tool in parsed.tools:
                if tool.name not in payload["allowed_tools"]:
                    raise ValueError("Original tool is not allowed")
                if ((tool.name == "list_equipment_candidates" and tool.reservation_id not in affected)
                        or (tool.name != "list_equipment_candidates" and tool.reservation_id is not None)):
                    raise ValueError("Original tool arguments are not allowed")
        except Exception:
            raise CoreFailure(ErrorCode.WORKFLOW_FAILED, "INJECTION_PRECONDITION_NOT_MET", attempted=True) from None
        changed = json.loads(response.content)
        changed["plan_id"] = self.marker
        content = json.dumps(changed, ensure_ascii=False, separators=(",", ":"))
        event = {
            "agent_id": request.agent_id, "request_digest": digest(request),
            "original_content_utf8_sha256": sha256(response.content.encode()).hexdigest(),
            "injected_content_utf8_sha256": sha256(content.encode()).hexdigest(),
            "original_core_content_digest": digest(response.content),
            "injected_core_content_digest": digest(content),
            "changed_fields": ["plan_id"], "original_plan_was_offered": True,
        }
        self.port.append_run_step(RunStepCreate(
            run_id=request.run_id, node="test.plan_id_injection", agent_id=request.agent_id,
            decision_reason="FAILURE_INJECTION_TEST: replaced only first valid planning plan_id; provider usage unchanged",
            input_digest=event["original_core_content_digest"], output_digest=event["injected_core_content_digest"],
            model_calls=0, retry_count=0,
        ))
        self.events.append(event)
        return response.model_copy(update={"content": content})


class PlanInjectionOrchestration:
    def __init__(self, settings):
        self.core = GearShiftOrchestrator(settings)
        self.client = None

    def run_incident(self, incident_id, run_id, readonly_port, model_client):
        self.client = OnePlanInjection(model_client, readonly_port)
        return self.core.run_incident(incident_id, run_id, readonly_port, self.client)


def _no_feasible_proven(report: dict) -> bool:
    """A manual stop alone does not prove that the candidate search has no plan."""
    if (report.get("plan_status") != "NEEDS_MANUAL" or report.get("changes")
            or report.get("proposal_id") is not None or report.get("error_code") is not None
            or report.get("reservations_unchanged_before_approval") is not True):
        return False
    steps = report.get("steps", [])
    return bool(
        steps and steps[-1]["node"] == "core.needs_manual"
        and steps[-1]["decision_reason"] == "NO_FEASIBLE_PLAN"
        and any(row["node"] == "core.candidate_dispatch" for row in steps)
        and any(row["node"] == "core.model" and row["agent_id"] == "impact-agent"
                and row["model_calls"] == 1 and row["error"] is None for row in steps)
        and any(row["node"] == "core.solve" and re.fullmatch(
            r"Bounded global search;visited=[1-9][0-9]*;plans=0;exhausted=True",
            row["decision_reason"],
        ) for row in steps)
    )


def accept_real(settings: ModelSettings, scenario: str, *, inject_plan: bool = False) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:10]
    directory = ROOT / "var/acceptance" / stamp
    directory.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1, "scenario": "feedback-plan-injection" if inject_plan else scenario,
        "execution_mode": "failure_injection_test" if inject_plan else "real_api", "fictional_only": True,
        "source": source_evidence(), "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "NOT_COMPLETED", "browser_acceptance": "NotRun", "database": str(directory / "fictional.db"),
    }
    started = time.monotonic()
    client = ObservedClient(LLMAdapter(settings))
    orchestration = PlanInjectionOrchestration(settings.limits) if inject_plan else GearShiftOrchestrator(settings.limits)
    try:
        database = prepare_database(directory, scenario)
        service = create_application(
            database.path, orchestration=orchestration, model_client=client,
            execution_mode=ExecutionMode.FAILURE_INJECTION_TEST if inject_plan else ExecutionMode.REAL_API,
            provider="openai_compatible", model_id=settings.model_id,
        )
        original = service.list_reservations()
        if scenario == "no-feasible":
            for equipment_id in ("cam-02", "cam-03"):
                service.register_equipment_failure(IncidentCreate(
                    equipment_id=equipment_id, effective_at="2026-11-10T00:00:00Z", reason="虚构验收：替代相机不可用"))
        incident = service.register_equipment_failure(IncidentCreate(
            equipment_id="cam-01", reason="虚构真实 API 验收：相机故障",
            effective_at="2026-11-11T00:00:00Z" if scenario == "success" else "2026-11-10T00:00:00Z",
        ))
        plan = service.start_workflow(incident.id)
        run = service.get_run(plan.run_id)
        steps = service.list_run_steps(plan.run_id)
        report.update(
            run_id=run.id, incident_id=incident.id, proposal_id=plan.proposal_id,
            plan_status=plan.status, model_calls=run.model_calls, retry_count=run.retry_count,
            error_code=plan.error.code if plan.error else None,
            reservations_unchanged_before_approval=(service.list_reservations() == original),
            changes=[row.model_dump(mode="json") for row in plan.changes],
            steps=[row.model_dump(mode="json") for row in steps],
            budget=service.get_model_budget(run.id).model_dump(mode="json"),
            hard_validation_passed=plan.validation.passed,
        )
        roles = {row.agent_id for row in steps if row.model_calls}
        if not report["reservations_unchanged_before_approval"]:
            raise OperatorError("BOOKINGS_CHANGED_BEFORE_APPROVAL")
        if inject_plan:
            injected = orchestration.client
            report["injection_events"] = injected.events if injected else []
            report["planner_inputs"] = injected.planner_inputs if injected else []
            report["injection_scope"] = "Controlled candidate-membership rejection; not a natural model error, Main hard-resource rejection or Verifier revise"
            report["expected_nominal_model_calls"] = 4
            report["status"] = "PASSED" if (
                plan.status == "WAIT_APPROVAL" and plan.validation.passed
                and injected is not None and len(injected.events) == 1
                and 1 <= run.retry_count <= 2 and 4 <= run.model_calls <= settings.limits.calls_per_run
                and roles == {"impact-agent", "planner-agent", "verifier-agent"}
                and len([s for s in steps if s.node == "test.plan_id_injection" and s.model_calls == 0]) == 1
                and any(s.node == "core.plan_rejected" for s in steps)
                and len([s for s in steps if s.node == "core.replan"]) == run.retry_count
                and len(injected.planner_inputs) >= 2
                and injected.planner_inputs[1]["retry_count"] == 1
                and "HARD_VALIDATION_REJECTED" in injected.planner_inputs[1]["feedback"]
            ) else "FAILED"
        elif scenario == "no-feasible":
            report["status"] = "PASSED" if _no_feasible_proven(report) else "FAILED"
        elif plan.status == "WAIT_APPROVAL" and roles == {"impact-agent", "planner-agent", "verifier-agent"}:
            if scenario == "parallel" and (len(plan.changes) != 3 or not any(
                    step.node == "core.candidate_dispatch" and "parallel_fan_out;tasks=3" in step.decision_reason
                    for step in steps)):
                raise OperatorError("THREE_BOOKING_PARALLEL_DISPATCH_MISSING")
            if scenario == "stale":
                service.register_equipment_failure(IncidentCreate(
                    equipment_id=plan.changes[0].to_equipment_id, effective_at="2026-11-10T00:00:00Z",
                    reason="虚构验收：生成方案后替代器材发生故障"))
            before_approval = service.list_reservations()
            key = "real-acceptance-" + uuid4().hex
            applied = service.approve_proposal(plan.proposal_id, "fictional-acceptance-admin", key)
            reloaded = create_application(database.path)
            after = reloaded.list_reservations()
            report["apply_status"] = applied.status
            report["decision"] = (decision.model_dump(mode="json")
                                  if (decision := reloaded.get_approval_decision(plan.proposal_id)) else None)
            if scenario == "stale":
                report["status"] = "PASSED" if applied.status == "STALE" and after == before_approval else "FAILED"
            else:
                before_by_id = {row.id: row for row in original}
                expected = dict(before_by_id)
                after_by_id = {row.id: row for row in after}
                for change in plan.changes:
                    observed = after_by_id[change.reservation_id]
                    if (observed.equipment_id != change.to_equipment_id
                            or observed.version != change.expected_reservation_version + 1
                            or observed.starts_at != change.starts_at or observed.ends_at != change.ends_at):
                        raise OperatorError("APPROVAL_READBACK_MISMATCH")
                    expected[change.reservation_id] = observed
                report["untargeted_bookings_unchanged"] = expected == after_by_id
                repeated = reloaded.approve_proposal(plan.proposal_id, "fictional-acceptance-admin", key)
                report["idempotent_readback"] = repeated == applied and reloaded.list_reservations() == after
                report["status"] = "PASSED" if (applied.status == "APPLIED" and report["idempotent_readback"]
                                                 and report["untargeted_bookings_unchanged"]) else "FAILED"
        else:
            report["status"] = "FAILED"
    except Exception:
        # Preserve failed DB/ledger and a fixed safe reason. Never dump exception data.
        report["status"] = "FAILED"
        report["operator_error"] = "ACCEPTANCE_FAILED_SEE_SANITIZED_RUN_RECORDS"
    finally:
        report["responses"] = client.responses
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        with (directory / "report.json").open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(json.dumps({"status": report["status"], "report": str(directory / "report.json"),
                          "model_calls": report.get("model_calls"), "error_code": report.get("error_code")}, ensure_ascii=False))
    return 0 if report["status"] == "PASSED" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check-config", "accept-real", "accept-feedback", "serve-real"))
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--scenario", choices=("success", "parallel", "no-feasible", "stale"), default="success")
    parser.add_argument("--thinking", choices=("default", "disabled", "enabled"), default=None)
    parser.add_argument("--account-protected", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    try:
        if Path.cwd().resolve() != ROOT or ROOT.drive.upper() != "D:":
            raise OperatorError("RUN_FROM_THIS_D_DRIVE_CAMPUSOPS_ROOT")
        live = args.command != "check-config"
        if live:
            require_live_authorization(args)
            from campusops.security.terminal import authorize_terminal
            authorize_terminal()
        settings = configure(args.env_file, enabled=live, thinking=args.thinking)
        if not live:
            print(json.dumps({"status": "CONFIG_VALIDATED_NO_REQUEST", "key_present": bool(settings.api_key),
                              "https_endpoint_valid": True, "model_id": settings.model_id,
                              "per_run_limit": settings.limits.calls_per_run, "daily_limit": settings.limits.daily_calls,
                              "model_enabled": False}, ensure_ascii=False))
            return 0
        if args.command == "serve-real":
            os.environ["DATABASE_PATH"] = str(ROOT / "var/data/campusops.db")
            os.execv(sys.executable, [sys.executable, "-m", "streamlit", "run", str(ROOT / "streamlit_app.py"),
                                     "--server.address", "127.0.0.1", "--server.headless", "true",
                                     "--browser.gatherUsageStats", "false"])
        return accept_real(settings, "success" if args.command == "accept-feedback" else args.scenario,
                           inject_plan=args.command == "accept-feedback")
    except (OperatorError, CampusOpsError):
        print("CONFIG_OR_AUTHORIZATION_INVALID: check local fields, limits and explicit confirmation; no secrets displayed")
        return 2
    except Exception:
        print("OPERATOR_UNAVAILABLE: no raw exception or credentials displayed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
