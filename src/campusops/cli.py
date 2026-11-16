"""Offline bootstrap commands. Never runs agents, installs providers or reads keys."""

import argparse
from importlib.util import find_spec
import os
from pathlib import Path

from campusops.bootstrap import create_application
from campusops.contracts.errors import CampusOpsError
from campusops.storage.database import Database
from campusops.storage.seed import seed_demo


def main() -> int:
    parser = argparse.ArgumentParser(prog="campusops")
    parser.add_argument("command", choices=("init-db", "seed-demo", "reset-demo", "status"))
    parser.add_argument("--db", default=os.environ.get("DATABASE_PATH", "var/data/campusops.db"))
    parser.add_argument("--seed", type=Path, default=Path("assets/demo_seed.json"))
    parser.add_argument("--confirm", default=None)
    args = parser.parse_args()
    try:
        from campusops.security.terminal import authorize_terminal
        authorize_terminal()
        database = Database(args.db)
        if args.command == "init-db":
            database.migrate()
            print(f"Initialized schema v1: {database.path}")
        elif args.command == "seed-demo":
            seed_demo(database, args.seed)
            print("Seeded fictional demo: 10 equipment, 20 bookings, 6 aliases, 3 agent configs; no AI records")
        elif args.command == "reset-demo":
            create_application(database.path, seed_asset=args.seed).reset_demo_data(args.confirm or "")
            print("Reset known fictional demo; previous DB backed up alongside the data file")
        else:
            service = create_application(database.path)
            print(f"DB: {database.path}")
            print(f"Equipment={len(service.list_equipment())}; Reservations={len(service.list_reservations())}")
            print(f"Incidents={len(service.list_incidents())}; Runs={len(service.list_runs())}")
            package = find_spec("campusops")
            source_root = Path(package.origin).parent
            core_present = (source_root / "agent_core/workflow.py").is_file()
            ui_present = (source_root / "ui/app.py").is_file()
            print(f"Agent Core source: {'present' if core_present else 'NOT_IMPLEMENTED'}; "
                  f"UI source: {'present' if ui_present else 'NOT_IMPLEMENTED'}")
            print("Integrated UI entry: streamlit_app.py; model calls: never enabled or made by status")
        return 0
    except CampusOpsError as exc:
        print(f"{exc.error.code}: {exc.error.message}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
