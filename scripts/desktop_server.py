"""Compatibility name for the independent review launcher."""
from reviewer_server import main
if __name__ == "__main__":
    raise SystemExit(main())