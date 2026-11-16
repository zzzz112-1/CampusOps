"""Process-local, session-bound opaque grants with monotonic expiration."""
from dataclasses import dataclass, field
import secrets
import threading
import time

from .errors import AccessDenied
from .state import SecurityStore


@dataclass
class _Grant:
    session_id: str
    config_id: str
    created: float
    activity: float


class SessionRegistry:
    def __init__(self, store=None, *, monotonic=None):
        self.store = store if store is not None else SecurityStore()
        self.clock = monotonic if monotonic is not None else time.monotonic
        self._grants: dict[str, _Grant] = {}
        self._lock = threading.RLock()

    def unlock(self, session_id: str, code: str) -> str:
        config_id = self.store.verify_and_consume(code)
        token = secrets.token_urlsafe(32)
        with self._lock:
            # Reauthentication supersedes only this Streamlit session's grant.
            self._grants = {k: v for k, v in self._grants.items() if v.session_id != session_id}
            now = self.clock()
            self._grants[token] = _Grant(session_id, config_id, now, now)
        return token

    def require(self, token: str, session_id: str, *, activity=False):
        with self._lock:
            grant = self._grants.get(token)
            now = self.clock()
            if grant is not None and grant.session_id != session_id:
                raise AccessDenied("LOCKED")
            if (grant is None
                    or now < grant.activity or now - grant.created >= 7200 or now - grant.activity >= 1800):
                self._grants.pop(token, None)
                raise AccessDenied("LOCKED")
            try:
                valid = self.store.configuration_id() == grant.config_id
            except AccessDenied:
                self._grants.pop(token, None)
                raise
            if not valid:
                self._grants.pop(token, None)
                raise AccessDenied("LOCKED")
            if activity:
                grant.activity = now

    def revoke(self, token: str):
        if not isinstance(token, str):
            return
        with self._lock:
            self._grants.pop(token, None)


@dataclass(repr=False)
class AccessContext:
    registry: SessionRegistry = field(repr=False)
    token: str = field(repr=False)
    session_id: str = field(repr=False)
    current_session: object = field(default=None, repr=False)

    def require(self, *, activity=False):
        if self.current_session is not None and self.current_session() != self.session_id:
            raise AccessDenied("LOCKED")
        self.registry.require(self.token, self.session_id, activity=activity)
