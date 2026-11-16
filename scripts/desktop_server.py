"""Main-owned Windows launcher bridge. Startup never calls a model."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


class DesktopStartError(Exception):
    """Fixed, non-secret startup reasons only."""


def prepare_session(session_dir: Path, *, enable_model: bool, account_protected: bool,
                    fresh_fictional: bool = False, thinking: str | None = None) -> dict:
    if enable_model and not account_protected:
        raise DesktopStartError("MODEL_REQUIRES_ACCOUNT_PROTECTION_CONFIRMATION")
    root = ROOT.resolve()
    session = session_dir.resolve()
    if (root.drive.upper() != "D:" or Path.cwd().resolve() != root
            or session.parent != root / "var/desktop"
            or not all((root / p).is_file() for p in ("AGENTS.md", "pyproject.toml", "docs/project-spec-v2.md"))):
        raise DesktopStartError("DESKTOP_PROJECT_OR_SESSION_PATH_INVALID")
    if (session / "session.json").exists():
        raise DesktopStartError("DESKTOP_SESSION_ALREADY_USED")
    session.mkdir(parents=True, exist_ok=True)
    for relative in (".tmp", ".cache/python", "var/data"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(root / ".tmp")
    os.environ["PYTHONPYCACHEPREFIX"] = str(root / ".cache/python")
    sys.pycache_prefix = str(root / ".cache/python")
    os.environ["CAMPUSOPS_LLM_ENABLED"] = "false"
    # The desktop package uses the existing escaped, read-only HTML tables.
    # It does not need optional native Arrow renderers to display service data.
    os.environ["CAMPUSOPS_TABLE_VIEW"] = "html"
    os.environ["STREAMLIT_CLIENT_TOOLBAR_MODE"] = "minimal"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    if enable_model:
        spec = importlib.util.spec_from_file_location("campusops_desktop_config", root / "scripts/campusops_operator.py")
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        config.configure(root / ".env", enabled=True, thinking=thinking)

    # No business DB is opened before authentication. The formal UI invokes
    # Main's authenticated desktop bootstrap after the entry gate succeeds.
    from campusops import __version__
    database_path = session / "fictional.db" if fresh_fictional else root / "var/data/campusops.db"
    if fresh_fictional and database_path.exists():
        raise DesktopStartError("FRESH_DESKTOP_DATABASE_ALREADY_EXISTS")
    os.environ["DATABASE_PATH"] = str(database_path)
    os.environ["CAMPUSOPS_DESKTOP_SESSION_DIR"] = str(session)
    metadata = {
        "schema_version": 1, "package_version": __version__, "root": str(root),
        "database": str(database_path), "session_dir": str(session),
        "model_enabled": enable_model, "fresh_fictional": fresh_fictional,
        "table_view": "html",
        "toolbar_mode": "minimal",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "startup_model_requests": 0,
    }
    with (session / "session.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return metadata


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--enable-model", action="store_true")
    parser.add_argument("--account-protected", action="store_true")
    parser.add_argument("--fresh-fictional", action="store_true")
    parser.add_argument("--thinking", choices=("default", "disabled", "enabled"), default=None)
    args = parser.parse_args(argv)
    stage = 'session_configuration'
    try:
        if not 1024 <= args.port <= 65535:
            raise DesktopStartError("DESKTOP_PORT_INVALID")
        metadata = prepare_session(args.session_dir, enable_model=args.enable_model,
                                   account_protected=args.account_protected,
                                   fresh_fictional=args.fresh_fictional, thinking=args.thinking)
        with (Path(metadata["session_dir"]) / "server.log").open("x", encoding="utf-8", buffering=1) as log:
            with redirect_stdout(log), redirect_stderr(log):
                stage = 'streamlit_import'
                from streamlit.web.cli import main as streamlit_main
                stage = 'local_server'
                sys.argv = ["streamlit", "run", str(ROOT / "streamlit_app.py"),
                            "--server.address", "127.0.0.1", "--server.port", str(args.port),
                            "--server.headless", "true", "--server.fileWatcherType", "none",
                            "--client.toolbarMode", "minimal",
                            "--browser.gatherUsageStats", "false"]
                return streamlit_main() or 0
    except Exception as error:
        # No provider/configuration exception or key is printed by this bridge.
        try:
            session = args.session_dir.resolve()
            if ROOT.resolve().drive.upper() == "D:" and session.parent == ROOT.resolve() / "var/desktop":
                session.mkdir(parents=True, exist_ok=True)
                with (session / "startup-error.json").open("x", encoding="utf-8") as handle:
                    import_name = getattr(error, 'name', None)
                    reason = (str(error) if isinstance(error, DesktopStartError) else
                              'DESKTOP_DEPENDENCY_IMPORT_FAILED' if isinstance(error, ImportError) else 'DESKTOP_START_UNAVAILABLE')
                    json.dump({"status": "FAILED", "reason": reason, "stage": stage,
                        "exception_type": type(error).__name__,
                        "native_module": import_name if import_name in ('_ctypes', '_sqlite3', '_ssl', '_socket') else None,
                        "guidance": "依赖加载失败时，请运行环境诊断并核对Windows应用控制记录；不要关闭安全防护。"}, handle, ensure_ascii=False)
        except Exception:
            pass
        if sys.stderr is not None:
            print("DESKTOP_START_UNAVAILABLE: inspect this session and local configuration", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
