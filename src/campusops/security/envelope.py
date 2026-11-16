"""Strict runtime-state codec. DPAPI supplies integrity; no enrollment capability."""
from dataclasses import asdict, dataclass
import json
import math
import re

from .errors import AccessDenied

APPLICATION = "CampusOps/GearShift/TOTP"
VERSION = 2
MAX_TIME = 4_102_444_800  # 2100-01-01 UTC; reject corrupt/unbounded timestamps.


@dataclass(frozen=True, repr=False)
class AuthState:
    version: int
    application: str
    config_id: str
    generation: int
    revision: int
    secret: str
    last_step: int
    failures: int
    cooldown_until: float


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def validate(data: dict) -> AuthState:
    try:
        if type(data) is not dict or set(data) != set(AuthState.__dataclass_fields__):
            raise ValueError
        if (type(data['version']) is not int or data['version'] != VERSION
                or data['application'] != APPLICATION
                or type(data['config_id']) is not str or not re.fullmatch(r'[0-9a-f]{32}', data['config_id'])
                or type(data['secret']) is not str or not re.fullmatch(r'[A-Z2-7]{32}', data['secret'])
                or type(data['generation']) is not int or not 1 <= data['generation'] < 2**63
                or type(data['revision']) is not int or not 0 <= data['revision'] < 2**63
                or type(data['last_step']) is not int or not -1 <= data['last_step'] <= MAX_TIME // 30
                or type(data['failures']) is not int or not 0 <= data['failures'] <= 5
                or type(data['cooldown_until']) not in (int, float)
                or not math.isfinite(data['cooldown_until']) or not 0 <= data['cooldown_until'] <= MAX_TIME
                or bool(data['cooldown_until']) != (data['failures'] == 5)):
            raise ValueError
        return AuthState(**data)
    except Exception:
        raise AccessDenied('STATE_UNAVAILABLE') from None


def unseal(protector, blob: bytes) -> AuthState:
    if type(blob) is not bytes or not 1 <= len(blob) <= 65536:
        raise AccessDenied('STATE_UNAVAILABLE')
    try:
        raw = protector.unprotect(blob)
        if type(raw) is not bytes or len(raw) > 8192:
            raise ValueError
        return validate(json.loads(raw, object_pairs_hook=_unique_object))
    except AccessDenied:
        raise
    except Exception:
        raise AccessDenied('STATE_UNAVAILABLE') from None


def seal(protector, state: AuthState) -> bytes:
    validated = validate(asdict(state))
    try:
        blob = protector.protect(json.dumps(asdict(validated), separators=(',', ':'), allow_nan=False).encode())
        if unseal(protector, blob) != validated:
            raise ValueError
        return blob
    except AccessDenied:
        raise
    except Exception:
        raise AccessDenied('STATE_UNAVAILABLE') from None
