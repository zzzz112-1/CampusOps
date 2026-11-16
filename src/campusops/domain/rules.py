"""Small read-only, versioned fictional rule catalog; never executes rule text."""

from hashlib import sha256
import json
from pathlib import Path

from pydantic import ValidationError

from campusops.contracts.errors import CampusOpsError
from campusops.contracts.models import BookingRuleCatalog, BookingRules, ErrorCode

DEFAULT_RULES = Path(__file__).resolve().parent / "data/gearshift-rules-v1.json"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate catalog field")
        result[key] = value
    return result


def load_booking_rules(path: Path | None = None) -> BookingRuleCatalog:
    source = Path(path or DEFAULT_RULES).resolve()
    if source.drive.upper() != "D:":
        raise CampusOpsError(ErrorCode.INVALID_STATE, "RULE_CATALOG_REQUIRES_D_DRIVE")
    try:
        if source.stat().st_size > 65536:
            raise ValueError("catalog too large")
        raw = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(raw, dict) or set(raw) != {"ruleset_id", "version", "source", "fictional", "rules"}:
            raise ValueError("unexpected catalog fields")
        # Validate and normalize before hashing; the text cannot override hard policy.
        provisional = BookingRuleCatalog(**BookingRules().model_dump(), **raw, fingerprint="0" * 64)
        payload = provisional.model_dump(mode="json", exclude={"fingerprint"})
        fingerprint = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode("utf-8")).hexdigest()
        return provisional.model_copy(update={"fingerprint": fingerprint})
    except (OSError, UnicodeError, ValueError, ValidationError):
        raise CampusOpsError(ErrorCode.INVALID_STATE, "RULE_CATALOG_INVALID_OR_UNAVAILABLE") from None
