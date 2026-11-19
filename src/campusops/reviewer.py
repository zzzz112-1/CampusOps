"""CampusOps demo data and model settings."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import ErrorCode

REVIEW_LABEL = "RC15-Review-1"
REVIEW_APP = "campusops-review-unlocked"
_initialization_lock = threading.RLock()
_runtime_files = {}


def _stop():
    raise CampusOpsError(ErrorCode.INVALID_STATE,
        "评审数据目录、来源标记或文件状态无法确认，已停止并保留原件。请使用完整新解压目录。")


def _plain_path(path):
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            _stop()
    if path.is_file() and path.stat().st_nlink != 1:
        _stop()


def project_root():
    module = Path(__file__).absolute()
    if module.parents[1].name == "src":
        return module.parents[2]
    if module.parents[3].name == "runtime":
        return module.parents[4]
    _stop()


@dataclass(frozen=True)
class ReviewerContext:
    """Filesystem destination context, not a login or authentication token."""
    root: Path

    @property
    def directory(self):
        return self.root / "var/reviewer"

    @property
    def database(self):
        return self.directory / "data/review.db"

    @property
    def budget(self):
        return self.directory / "model-budget"

    @property
    def seed(self):
        return self.root / "assets/demo_seed.json"

    def require(self, *, activity=False):
        # Activity is only an existing UI callback signature, not session expiry.
        if (not self.root.is_absolute() or self.root.drive.casefold() != "d:"
                or self.root == Path("D:/CampusOps") or self.root != self.root.resolve()):
            _stop()
        for path in (self.root, self.directory, self.database, self.budget, self.seed):
            _plain_path(path)
        try:
            marker = json.loads((self.root / "reviewer-build.json").read_text(encoding="utf-8"))
            if marker != {"application": REVIEW_APP, "label": REVIEW_LABEL,
                          "version": "0.2.0rc15.post1", "seed_sha256": sha256(self.seed.read_bytes()).hexdigest()}:
                _stop()
            if not self.database.resolve().is_relative_to(self.directory.resolve()):
                _stop()
            for directory in (self.database.parent, self.budget,
                              self.directory / "logs", self.directory / "tmp",
                              self.directory / "cache", self.directory / "cache/python"):
                _plain_path(directory)
            # Check persistence files without repeatedly walking framework caches.
            for directory in (self.database.parent, self.budget):
                if directory.exists():
                    for item in directory.iterdir():
                        _plain_path(item)
        except (OSError, ValueError, TypeError):
            _stop()


def get_context(root=None):
    context = ReviewerContext(Path(root).absolute() if root is not None else project_root())
    context.require()
    return context


def initialize_data(context):
    """Initialize only a newly claimed destination; never repair unknown files."""
    from campusops.storage.database import Database
    from campusops.storage.seed import DATASET_ID, seed_demo
    context.require()
    base = context.directory
    marker_path = base / "instance.json"
    with _initialization_lock:
        context.require()
        if base.exists() and any(base.iterdir()) and not marker_path.is_file():
            _stop()
        if marker_path.exists():
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                if (set(marker) != {"application", "format", "root", "instance_id", "ready"}
                        or marker["application"] != REVIEW_APP or marker["format"] != 1
                        or marker["root"] != str(context.root) or marker["ready"] is not True
                        or not isinstance(marker["instance_id"], str)
                        or len(marker["instance_id"]) != 32 or not context.database.is_file()):
                    _stop()
                database = Database(context.database)
                with database.connect(readonly=True) as connection:
                    metadata = dict(connection.execute("SELECT key,value FROM project_metadata"))
                    if (metadata.get("dataset_id") != DATASET_ID
                            or metadata.get("review_instance_id") != marker["instance_id"]):
                        _stop()
                database.migrate()
                return context
            except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                _stop()
        base.mkdir(parents=True, exist_ok=True)
        marker = {"application": REVIEW_APP, "format": 1, "root": str(context.root),
                  "instance_id": uuid4().hex, "ready": False}
        try:
            with marker_path.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(marker, handle)
            # Interrupted bootstrap stays unready and preserves its partial files.
            temporary = base / ("initializing-" + marker["instance_id"] + ".db")
            database = Database(temporary)
            database.migrate()
            seed_demo(database, context.seed)
            with database.transaction() as connection:
                connection.execute("INSERT INTO project_metadata VALUES ('review_instance_id',?)",
                                   (marker["instance_id"],))
            context.database.parent.mkdir(parents=True, exist_ok=True)
            if context.database.exists():
                _stop()
            temporary.rename(context.database)
            marker["ready"] = True
            marker_path.write_bytes((json.dumps(marker) + "\n").encode())
        except (OSError, sqlite3.Error):
            _stop()
        return context


def start_reviewer_runtime(context):
    """Start a workbench once per process, holding its data lock until process exit."""
    from campusops.services.application import ApplicationService
    from campusops.storage.database import Database

    with _initialization_lock:
        if context.root in _runtime_files:
            return
        initialize_data(context)
        lock_path = context.database.parent / "workbench.lock"
        _plain_path(lock_path)
        lock_file = lock_path.open("a+b")
        try:
            try:
                if os.name == "nt":
                    import msvcrt
                    # The Windows lock covers the first byte. Initialize it only
                    # when empty; competing processes lock the same byte.
                    if lock_file.tell() == 0:
                        lock_file.write(b"\0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CampusOpsError(ErrorCode.INVALID_STATE,
                    "无法取得当前演示目录的运行锁。请先关闭此目录中正在运行的 CampusOps，再重新启动。") from exc
            ApplicationService(Database(context.database)).recover_interrupted_plans()
        except BaseException:
            lock_file.close()
            raise
        _runtime_files[context.root] = lock_file


def configure_environment(context):
    """Old application variables are ignored; only REVIEW model opt-in applies."""
    context.require()
    for name in list(os.environ):
        if name.startswith("MODEL_") or name in ("DATABASE_PATH", "CAMPUSOPS_LLM_ENABLED",
                "CAMPUSOPS_BUDGET_PROJECT_ROOT", "CAMPUSOPS_DESKTOP_SESSION_DIR", "REPLAN_MAX"):
            os.environ.pop(name, None)
    os.environ["DATABASE_PATH"] = str(context.database)
    os.environ["CAMPUSOPS_LLM_ENABLED"] = "false"
    if os.environ.get("CAMPUSOPS_REVIEW_LLM_ENABLED", "false").lower() == "true":
        for suffix in ("API_KEY", "BASE_URL", "ID", "THINKING", "TIMEOUT_SECONDS",
                       "MAX_OUTPUT_TOKENS", "CALLS_PER_RUN_MAX", "DAILY_CALLS_MAX"):
            name = "CAMPUSOPS_REVIEW_MODEL_" + suffix
            if name in os.environ:
                os.environ["MODEL_" + suffix] = os.environ[name]
        if "CAMPUSOPS_REVIEW_REPLAN_MAX" in os.environ:
            os.environ["REPLAN_MAX"] = os.environ["CAMPUSOPS_REVIEW_REPLAN_MAX"]
        os.environ["CAMPUSOPS_LLM_ENABLED"] = "true"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["CAMPUSOPS_TABLE_VIEW"] = "html"
    for relative in ("logs", "cache/python", "tmp"):
        (context.directory / relative).mkdir(parents=True, exist_ok=True)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(context.directory / "tmp")
    os.environ["PYTHONPYCACHEPREFIX"] = str(context.directory / "cache/python")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["XDG_CACHE_HOME"] = str(context.directory / "cache")


def run_ui():
    import streamlit as st
    from campusops.bootstrap import create_configured_application
    from campusops.ui.app import render_app, _show_error
    from campusops.security.streamlit_gate import require_ui_access
    context = require_ui_access()
    if context is None:
        return
    try:
        start_reviewer_runtime(context)
        service = create_configured_application()
    except CampusOpsError as exc:
        _show_error(exc.error)
        return
    except Exception:
        st.error("评审服务未能启动，请核对评审目录及本机模型配置。")
        return
    render_app(service, reset_target=context.database)
