"""Review branch composition; normal configured entry owns isolated bootstrap."""

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
    """Initialize known reviewer data and compose the unchanged Core/Main services.

    Only the independent review configuration can enable the single real adapter.
    No model is called here and no original .env or TOTP state is accessed.
    """
    from campusops.agent_core import CoreSettings, GearShiftOrchestrator, LLMAdapter

    from campusops.reviewer import get_context, initialize_data, configure_environment
    context = get_context()
    if database_path is not None and Path(database_path).absolute() != context.database:
        raise ValueError("Reviewer database destination mismatch")
    if seed_asset is not None and Path(seed_asset).absolute() != context.seed:
        raise ValueError("Reviewer seed destination mismatch")
    initialize_data(context)
    configure_environment(context)
    settings = CoreSettings.from_env()
    client = None
    if os.environ.get("CAMPUSOPS_LLM_ENABLED", "false").lower() == "true":
        from campusops.agent_core.budget import BudgetLedger
        from campusops.agent_core.config import ModelSettings
        client = LLMAdapter(ModelSettings.from_env(), budget=BudgetLedger(
            directory=context.budget, project_root=context.root))
    return create_application(
        context.database, orchestration=GearShiftOrchestrator(settings), model_client=client,
        execution_mode=ExecutionMode.REAL_API,
        provider="openai_compatible" if client is not None else None,
        model_id=client.settings.model_id if client is not None else None,
        seed_asset=context.seed,
    )
