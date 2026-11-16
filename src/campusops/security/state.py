"""Runtime only: validate existing state and atomically consume codes.

Enrollment, migration, replacement and recovery belong to private maintenance.
Missing and legacy files never create a binding through a normal entry.
"""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import math
from pathlib import Path
import re
import sqlite3
import time

import pyotp

from .dpapi import WindowsDPAPI
from .envelope import MAX_TIME, VERSION, seal, unseal
from .errors import AccessDenied

STATE_PATH = Path('D:/CampusOps/var/security/totp-state.sqlite3')
COLUMNS = ('id', 'version', 'config_id', 'protected_secret', 'last_step', 'failures', 'cooldown_until')


def totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret, digits=6, interval=30, digest=hashlib.sha1)


def matched_step(secret: str, code: str, now: float) -> int:
    if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < MAX_TIME - 60:
        raise AccessDenied('STATE_UNAVAILABLE')
    if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code.strip()):
        raise AccessDenied('INVALID_CODE')
    step = int(now // 30)
    generator = totp(secret)
    for offset in (0, -1, 1):
        candidate = step + offset
        if candidate >= 0 and pyotp.utils.strings_equal(generator.at(candidate * 30), code.strip()):
            return candidate
    raise AccessDenied('INVALID_CODE')


class SecurityStore:
    def __init__(self, path: Path = STATE_PATH, *, protector=None, wall_clock=None):
        self.path = Path(path).absolute()
        self.protector = protector if protector is not None else WindowsDPAPI()
        self.clock = wall_clock if wall_clock is not None else time.time

    def _path_check(self):
        if self.path.drive.upper() != 'D:' or any(p.is_symlink() or p.is_junction() for p in (self.path, *self.path.parents)):
            raise AccessDenied('STATE_UNAVAILABLE')

    @contextmanager
    def connection(self, *, write=False):
        connection = None
        try:
            self._path_check()
            if not self.path.is_file():
                raise AccessDenied('NOT_CONFIGURED')
            if self.path.stat().st_size > 1048576:
                raise AccessDenied('STATE_UNAVAILABLE')
            connection = sqlite3.connect(self.path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                                         uri=True, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA trusted_schema=OFF')
            if write:
                connection.execute('BEGIN IMMEDIATE')
            else:
                connection.execute('PRAGMA query_only=ON')
            yield connection
            if write:
                connection.commit()
        except Exception as error:
            if connection is not None and write:
                try:
                    connection.rollback()
                except Exception:
                    pass
            if isinstance(error, AccessDenied):
                raise
            raise AccessDenied('STATE_UNAVAILABLE') from None
        finally:
            if connection is not None:
                connection.close()

    def _row(self, connection):
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise AccessDenied('STATE_UNAVAILABLE')
        objects = [tuple(row) for row in connection.execute(
            "SELECT type,name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name")]
        if objects != [('table', 'binding')]:
            # A view or trigger must not silently discard/restore consumption.
            raise AccessDenied('STATE_UNAVAILABLE')
        columns = tuple(row[1] for row in connection.execute('PRAGMA table_info(binding)'))
        rows = connection.execute('SELECT * FROM binding').fetchall() if columns == COLUMNS else []
        if len(rows) != 1 or rows[0]['id'] != 1:
            raise AccessDenied('STATE_UNAVAILABLE')
        row = rows[0]
        if row['version'] == 1:
            raise AccessDenied('MIGRATION_REQUIRED')
        if type(row['version']) is not int or row['version'] != VERSION:
            raise AccessDenied('STATE_UNAVAILABLE')
        state = unseal(self.protector, row['protected_secret'])
        # Outer compatibility mirrors cannot lower authoritative DPAPI limits.
        for key in ('version', 'config_id', 'last_step', 'failures', 'cooldown_until'):
            value = getattr(state, key)
            types = (int, float) if key == 'cooldown_until' else (type(value),)
            if type(row[key]) not in types or row[key] != value:
                raise AccessDenied('STATE_UNAVAILABLE')
        return row, state

    def configuration_id(self) -> str:
        with self.connection() as connection:
            _, state = self._row(connection)
            return state.config_id

    def cooldown_seconds(self) -> int:
        with self.connection() as connection:
            _, state = self._row(connection)
            now = self.clock()
            if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < MAX_TIME - 60:
                raise AccessDenied('STATE_UNAVAILABLE')
            return max(0, math.ceil(state.cooldown_until - now))

    def verify_and_consume(self, code: str) -> str:
        rejected = None
        accepted = None
        with self.connection(write=True) as connection:
            _, state = self._row(connection)
            now = self.clock()
            if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < MAX_TIME - 60:
                raise AccessDenied('STATE_UNAVAILABLE')
            if now < state.cooldown_until:
                raise AccessDenied('COOLDOWN')
            try:
                step = matched_step(state.secret, code, now)
                if step <= state.last_step:
                    raise AccessDenied('REPLAYED')
            except AccessDenied as error:
                rejected = error
                failures = (0 if state.cooldown_until else state.failures) + 1
                deadline = now + 60 if failures >= 5 else 0.0
                updated = replace(state, revision=state.revision + 1,
                                  failures=min(failures, 5), cooldown_until=deadline)
                if deadline:
                    rejected = AccessDenied('COOLDOWN')
            else:
                updated = replace(state, revision=state.revision + 1, last_step=step,
                                  failures=0, cooldown_until=0.0)
                accepted = state.config_id
            protected = seal(self.protector, updated)
            cursor = connection.execute('UPDATE binding SET protected_secret=?,last_step=?,failures=?,cooldown_until=? WHERE id=1',
                                        (protected, updated.last_step, updated.failures, updated.cooldown_until))
            if cursor.rowcount != 1 or self._row(connection)[1] != updated:
                raise AccessDenied('STATE_UNAVAILABLE')
        # A commit/encryption/write failure cannot return success or mint a grant.
        if rejected is not None:
            raise rejected
        return accepted
