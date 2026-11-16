"""Main-only database lifecycle with explicit transaction ownership."""

from contextlib import contextmanager
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import sqlite3

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import ErrorCode
from campusops.domain.rules import load_booking_rules


class Database:
    def __init__(self, path: str | Path, *, rule_catalog_path: Path | None = None):
        self.path = Path(path).resolve()
        self._rule_catalog_path = rule_catalog_path

    def get_booking_rules(self):
        return load_booking_rules(self._rule_catalog_path)

    @contextmanager
    def connect(self, *, readonly: bool = False):
        # mode=rw prevents a typo from silently creating an empty business DB.
        mode = "ro" if readonly else "rw"
        connection = sqlite3.connect(
            self.path.as_uri() + f"?mode={mode}", uri=True,
            isolation_level=None, timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        try:
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "schema_migrations" not in tables:
                raise CampusOpsError(ErrorCode.INVALID_STATE, "Refusing to modify an unrecognized existing database")
            migration = files("campusops.storage").joinpath("migrations/001_initial.sql")
            content = migration.read_text(encoding="utf-8")
            checksum = sha256(content.encode("utf-8")).hexdigest()
            if "schema_migrations" in tables:
                applied = connection.execute("SELECT version,checksum FROM schema_migrations ORDER BY version").fetchall()
                if applied != [(1, checksum)]:
                    raise CampusOpsError(ErrorCode.INVALID_STATE, "Unknown or modified schema migration; migration must be reviewed by Main")
                return
            # Trusted repository SQL only. executescript starts its own explicit transaction.
            connection.executescript("BEGIN IMMEDIATE;\n" + content)
            connection.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (1, "001_initial.sql", checksum))
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
