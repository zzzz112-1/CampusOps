"""Main's offline fixture bootstrap; no public CLI authentication bypass."""
from pathlib import Path
from campusops.storage.database import Database
from campusops.storage.seed import seed_demo


def initialize_database(path):
    database = Database(path)
    database.migrate()
    seed_demo(database, Path(__file__).resolve().parents[1] / "assets/demo_seed.json")


class PreauthorizedBusinessTestContext:
    """Business UI tests only; separate security tests exercise actual grants."""
    def require(self, *, activity=False):
        return None
