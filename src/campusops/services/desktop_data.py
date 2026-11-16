"""Authenticated desktop bootstrap; never touches security or budget state."""
import json
import os
from pathlib import Path

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import ErrorCode


def initialize_desktop_data(access, root: Path):
    access.require()  # Before paths, DB access, migration or seed.
    selected = os.environ.get("CAMPUSOPS_DESKTOP_SESSION_DIR")
    if not selected:
        return  # Source entry retains its explicit bootstrap workflow.
    root = Path(root).absolute()
    session = Path(selected).absolute()
    if (root.drive.upper() != "D:" or session.parent != root / "var/desktop"
            or any(p.is_symlink() or p.is_junction() for p in (session, *session.parents))):
        raise CampusOpsError(ErrorCode.INVALID_STATE, "桌面会话路径无效，未初始化数据。")
    try:
        metadata = json.loads((session / "session.json").read_text(encoding="utf-8"))
        expected = session / "fictional.db" if metadata["fresh_fictional"] else root / "var/data/campusops.db"
        if Path(metadata["root"]) != root or Path(metadata["database"]) != expected:
            raise ValueError
        if Path(os.environ["DATABASE_PATH"]).absolute() != expected:
            raise ValueError
    except Exception:
        raise CampusOpsError(ErrorCode.INVALID_STATE, "桌面会话记录无效，未初始化数据。") from None
    from campusops.storage.database import Database
    from campusops.storage.seed import DATASET_ID, seed_demo
    database = Database(expected)
    if database.path.exists():
        try:
            with database.connect(readonly=True) as connection:
                row = connection.execute("SELECT value FROM project_metadata WHERE key='dataset_id'").fetchone()
                if row is None or row[0] != DATASET_ID:
                    raise ValueError
        except Exception:
            raise CampusOpsError(ErrorCode.INVALID_STATE,
                "桌面数据库不属于已识别的虚构数据，已保留原件。(DESKTOP_DATABASE_NOT_RECOGNIZED)") from None
        database.migrate()
    else:
        database.migrate()
        seed_demo(database, root / "assets/demo_seed.json")
