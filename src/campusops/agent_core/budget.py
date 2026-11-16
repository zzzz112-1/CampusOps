"""Append-only model-attempt reservations, independent of the resettable demo DB.

This is Core's private quota journal, never a booking/proposal database. A consumed
reservation is not refunded on a timeout, crash or provider error. Corruption and
lock contention fail closed. No journal repair, rotation or deletion is automatic.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import tomllib

from campusops.agent_core.config import CoreSettings
from campusops.agent_core.errors import CoreFailure
from campusops.contracts.models import ErrorCode, Identifier, ModelBudgetSnapshot, ServiceError
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1, le=1)
    utc_date: str
    run_id: Identifier
    daily_count: int = Field(ge=1)
    run_count: int = Field(ge=1)


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_run_id = TypeAdapter(Identifier)


def _validated_project_root(candidate: Path) -> Path:
    """Validate only the supplied context, without walking parent directories."""
    try:
        if not candidate.is_absolute() or (os.name == "nt" and candidate.drive.casefold() != "d:"):
            raise ValueError("An absolute D-drive project context is required")
        root = candidate.resolve(strict=True)
        if os.name == "nt" and root.drive.casefold() != "d:":
            raise ValueError("Resolved project context left the D drive")
        if not all((root / marker).is_file() for marker in ("AGENTS.md", "docs/project-spec-v2.md", "pyproject.toml")):
            raise ValueError("Missing CampusOps context markers")
        with (root / "pyproject.toml").open("rb") as handle:
            if tomllib.load(handle).get("project", {}).get("name") != "campusops":
                raise ValueError("Not a CampusOps project context")
        return root
    except Exception:
        raise CoreFailure(ErrorCode.INVALID_INPUT, "BUDGET_PROJECT_CONTEXT_REQUIRED") from None


def _project_root(explicit: Path | None) -> Path:
    module = Path(__file__).resolve()
    if (len(module.parents) < 3 or module.parent.name != "agent_core"
            or module.parents[1].name != "campusops"):
        raise CoreFailure(ErrorCode.INVALID_INPUT, "BUDGET_PROJECT_CONTEXT_REQUIRED")
    container = module.parents[2]
    if container.name == "src":
        # Editable imports retain their original tree even if the process cwd
        # changes. Never redirect an editable client into another tree's ledger.
        root = _validated_project_root(container.parent)
        if explicit is not None and _validated_project_root(Path(explicit)) != root:
            raise CoreFailure(ErrorCode.INVALID_INPUT, "BUDGET_PROJECT_ROOT_MISMATCH")
        return root
    if container.name in ("site-packages", "dist-packages"):
        # Installed code has no source-tree anchor. Require an explicit current
        # CampusOps context (or private caller argument), never the venv/Lib dir.
        return _validated_project_root(Path(explicit) if explicit is not None else Path.cwd())
    raise CoreFailure(ErrorCode.INVALID_INPUT, "BUDGET_PROJECT_CONTEXT_REQUIRED")


class BudgetLedger:
    def __init__(self, directory: Path | None = None, *, project_root: Path | None = None):
        worktree = _project_root(project_root)
        self.directory = Path(directory) if directory is not None else worktree / "var" / "model-budget"
        if os.name == "nt" and self.directory.drive and self.directory.drive.casefold() != "d:":
            raise CoreFailure(ErrorCode.INVALID_INPUT, "BUDGET_DIRECTORY_OUTSIDE_WORKTREE")
        self.directory = self.directory.resolve()
        if not self.directory.is_relative_to(worktree):
            raise CoreFailure(ErrorCode.INVALID_INPUT, "BUDGET_DIRECTORY_OUTSIDE_WORKTREE")
        with _locks_guard:
            self._lock = _locks.setdefault(str(self.directory), threading.Lock())

    @contextmanager
    def _exclusive(self):
        if not self._lock.acquire(timeout=5):
            raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "BUDGET_LOCK_UNAVAILABLE")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / "usage.lock").open("a+b") as handle:
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock.release()

    def _read_counts(self) -> tuple[dict[str, int], dict[str, int]]:
        """Caller holds the same exclusive lock used for reservations and reads."""
        path = self.directory / "usage.jsonl"
        daily: dict[str, int] = {}
        runs: dict[str, int] = {}
        if path.exists():
            data = path.read_bytes()
            if data and not data.endswith(b"\n"):
                raise ValueError("Incomplete journal")
            for line in data.splitlines():
                entry = _Entry.model_validate_json(line)
                if datetime.strptime(entry.utc_date, "%Y-%m-%d").date().isoformat() != entry.utc_date:
                    raise ValueError("Invalid journal date")
                if (entry.daily_count != daily.get(entry.utc_date, 0) + 1
                        or entry.run_count != runs.get(entry.run_id, 0) + 1):
                    raise ValueError("Invalid journal sequence")
                daily[entry.utc_date] = entry.daily_count
                runs[entry.run_id] = entry.run_count
        return daily, runs

    def get_model_budget(self, run_id: str, limits: CoreSettings) -> ModelBudgetSnapshot:
        try:
            _run_id.validate_python(run_id)
        except Exception:
            raise CoreFailure(ErrorCode.INVALID_INPUT, "INVALID_BUDGET_RUN_ID") from None
        try:
            with self._exclusive():
                daily, runs = self._read_counts()
                observed = datetime.now(timezone.utc)
                day = observed.date()
                run_reserved = runs.get(run_id, 0)
                daily_reserved = daily.get(day.isoformat(), 0)
                return ModelBudgetSnapshot(
                    run_id=run_id, status="AVAILABLE", utc_date=day, observed_at=observed,
                    per_run_limit=limits.calls_per_run, daily_limit=limits.daily_calls,
                    run_reserved=run_reserved, daily_reserved=daily_reserved,
                    run_remaining=max(0, limits.calls_per_run - run_reserved),
                    daily_remaining=max(0, limits.daily_calls - daily_reserved),
                )
        except Exception:
            # Do not expose filesystem paths, corrupt entries or raw lock errors.
            observed = datetime.now(timezone.utc)
            return ModelBudgetSnapshot(
                run_id=run_id, status="UNAVAILABLE", utc_date=observed.date(), observed_at=observed,
                error=ServiceError(code=ErrorCode.BUDGET_EXCEEDED, message="BUDGET_LEDGER_UNAVAILABLE_OR_INVALID"),
            )

    def reserve(self, run_id: str, limits: CoreSettings) -> None:
        try:
            with self._exclusive():
                path = self.directory / "usage.jsonl"
                daily, runs = self._read_counts()
                today = datetime.now(timezone.utc).date().isoformat()
                if runs.get(run_id, 0) >= limits.calls_per_run:
                    raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "RUN_MODEL_BUDGET_EXCEEDED")
                if daily.get(today, 0) >= limits.daily_calls:
                    raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "DAILY_MODEL_BUDGET_EXCEEDED")
                entry = _Entry(version=1, utc_date=today, run_id=run_id,
                               daily_count=daily.get(today, 0) + 1, run_count=runs.get(run_id, 0) + 1)
                # Append and fsync BEFORE the network attempt. Never truncate/replace a ledger.
                with path.open("ab") as handle:
                    handle.write(entry.model_dump_json().encode("utf-8") + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        except CoreFailure:
            raise
        except Exception:
            raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "BUDGET_LEDGER_UNAVAILABLE_OR_INVALID") from None
