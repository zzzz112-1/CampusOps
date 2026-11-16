"""Dependency-free input bound, independent of provider-specific tokenizers."""

import json

from campusops.agent_core.errors import CoreFailure
from campusops.contracts.models import ErrorCode


MAX_MODEL_INPUT_BYTES = 1048576


def require_bounded_input(payload: dict) -> int:
    """Bound canonical ASCII-escaped JSON UTF-8 bytes; never log or truncate it."""
    try:
        size = len(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True, allow_nan=False).encode("utf-8"))
    except Exception:
        raise CoreFailure(ErrorCode.INVALID_INPUT, "INVALID_MODEL_REQUEST") from None
    if size > MAX_MODEL_INPUT_BYTES:
        raise CoreFailure(ErrorCode.INVALID_INPUT, "MODEL_INPUT_TOO_LARGE")
    return size
