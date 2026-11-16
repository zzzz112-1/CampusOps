"""Main-owned composition factory. Does not seed, migrate or call any model."""

import os
from pathlib import Path

from campusops.contracts.models import ExecutionMode
from campusops.contracts.ports import ApplicationPort, ModelClientPort, OrchestrationPort
from campusops.services.application import ApplicationService
from campusops.storage.database import Database


def create_application(
    database_path: str | Path | None = None, *, orchestration: OrchestrationPort | None = None,
    model_client: ModelClientPort | None = None, execution_mode: ExecutionMode = ExecutionMode.SYSTEM,
    provider: str | None = None, model_id: str | None = None, seed_asset: Path | None = None,
) -> ApplicationPort:
    path = database_path or os.environ.get("DATABASE_PATH") or Path.cwd() / "var/data/campusops.db"
    return ApplicationService(
        Database(path), orchestration=orchestration, model_client=model_client,
        execution_mode=execution_mode, provider=provider, model_id=model_id,
        seed_asset=seed_asset or Path.cwd() / "assets/demo_seed.json",
    )


def create_configured_application(
    database_path: str | Path | None = None, *, seed_asset: Path | None = None,
) -> ApplicationPort:
    """Main's integrated runtime; explicit operator opt-in before reading model settings.

    The original low-level factory stays unchanged. With no opt-in the installed
    Core is wired but has no client: impacted runs fail PROVIDER_UNAVAILABLE with
    zero model calls. No-impact runs still finish deterministically. This factory
    never initializes data, loads .env, calls a model or selects another provider.
    """
    from campusops.agent_core import CoreSettings, GearShiftOrchestrator, LLMAdapter

    settings = CoreSettings.from_env()
    client = None
    if os.environ.get("CAMPUSOPS_LLM_ENABLED", "false").lower() == "true":
        budget_context = os.environ.get("CAMPUSOPS_BUDGET_PROJECT_ROOT")
        if budget_context:
            # Reuse the existing validated authority when a package is moved for
            # acceptance. Core's D-drive/project/directory guards remain intact.
            # The flag cannot enable a model or authorize a fee by itself.
            from campusops.agent_core.budget import BudgetLedger
            from campusops.agent_core.config import ModelSettings
            from campusops.agent_core.errors import CoreFailure
            from campusops.contracts.models import ErrorCode
            ledger = BudgetLedger(project_root=Path(budget_context))
            if not (ledger.directory / "usage.jsonl").is_file():
                raise CoreFailure(ErrorCode.INVALID_STATE, "EXISTING_BUDGET_AUTHORITY_REQUIRED")
            client = LLMAdapter(ModelSettings.from_env(), budget=ledger)
        else:
            client = LLMAdapter.from_env()
    return create_application(
        database_path, orchestration=GearShiftOrchestrator(settings), model_client=client,
        execution_mode=ExecutionMode.REAL_API,
        provider="openai_compatible" if client is not None else None,
        model_id=client.settings.model_id if client is not None else None,
        seed_asset=seed_asset,
    )
