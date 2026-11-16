"""Reviewer commands, scoped to the automatically initialized fictional DB."""
import argparse
from pathlib import Path
from campusops.contracts.errors import CampusOpsError
from campusops.reviewer import get_context, initialize_data, configure_environment

def main():
    parser = argparse.ArgumentParser(prog="campusops")
    parser.add_argument("command", choices=("init-db", "seed-demo", "reset-demo", "status"))
    parser.add_argument("--db", type=Path)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        context = get_context()
        if args.db is not None and args.db.absolute() != context.database:
            parser.error("Review CLI only accepts this package's var/reviewer/data/review.db")
        if args.seed is not None and args.seed.absolute() != context.seed:
            parser.error("Review CLI only accepts this package's known fictional seed")
        initialize_data(context)
        configure_environment(context)
        from campusops.bootstrap import create_application
        from campusops.security.service import protect_service
        service = protect_service(create_application(context.database, seed_asset=context.seed), context)
        if args.command == "reset-demo":
            service.reset_demo_data(args.confirm)
        print("CampusOps RC15-Review-1; independent fictional data; no TOTP")
        print(f"Equipment={len(service.list_equipment())}; Reservations={len(service.list_reservations())}")
        print(f"Incidents={len(service.list_incidents())}; Runs={len(service.list_runs())}")
        return 0
    except CampusOpsError as error:
        print(error.error.message)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
