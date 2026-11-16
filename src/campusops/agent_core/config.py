"""Process-local configuration; never loads .env or chooses a fallback provider."""

from dataclasses import dataclass, field
import math
import os
import re
from typing import Literal
from urllib.parse import urlsplit

from campusops.agent_core.errors import CoreFailure, invalid
from campusops.contracts.models import ErrorCode


def _integer(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, str(default))
    if not re.fullmatch(r"[0-9]+", raw):
        raise invalid(f"INVALID_CONFIGURATION: {name}")
    value = int(raw)
    if not low <= value <= high:
        raise invalid(f"INVALID_CONFIGURATION: {name}")
    return value


@dataclass(frozen=True)
class CoreSettings:
    calls_per_run: int = 6
    daily_calls: int = 30
    max_replans: int = 2
    search_nodes: int = 10000
    offered_plans: int = 8
    parallel_queries: int = 4
    task_timeout_seconds: float = 180.0

    def __post_init__(self):
        for name, low, high in (
            ("calls_per_run", 1, 100), ("daily_calls", 1, 10000),
            ("max_replans", 0, 2), ("search_nodes", 1, 100000),
            ("offered_plans", 1, 20), ("parallel_queries", 1, 10),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise invalid(f"INVALID_CONFIGURATION: {name}")
        if (type(self.task_timeout_seconds) not in (int, float)
                or not math.isfinite(self.task_timeout_seconds)
                or not 0 < self.task_timeout_seconds <= 3600):
            raise invalid("INVALID_CONFIGURATION: task_timeout_seconds")

    @classmethod
    def from_env(cls):
        return cls(
            calls_per_run=_integer("MODEL_CALLS_PER_RUN_MAX", 6, 1, 100),
            daily_calls=_integer("MODEL_DAILY_CALLS_MAX", 30, 1, 10000),
            max_replans=_integer("REPLAN_MAX", 2, 0, 2),
        )


@dataclass(frozen=True)
class ModelSettings:
    api_key: str = field(repr=False)
    base_url: str
    model_id: str
    enabled: bool = False
    timeout_seconds: float = 30.0
    max_output_tokens: int = 1600
    limits: CoreSettings = field(default_factory=CoreSettings)
    thinking: Literal["default", "disabled", "enabled"] = "default"

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise invalid("INVALID_CONFIGURATION: enabled must be an explicit boolean")
        if self.thinking not in ("default", "disabled", "enabled"):
            raise invalid("INVALID_CONFIGURATION: MODEL_THINKING")
        try:
            url = urlsplit(self.base_url)
            valid_url = (url.scheme == "https" and bool(url.hostname) and not url.username
                         and not url.password and not url.query and not url.fragment)
            # Parsing the port also rejects malformed host:port strings.
            _ = url.port
        except ValueError:
            valid_url = False
        if not valid_url:
            raise invalid("INVALID_CONFIGURATION: MODEL_BASE_URL must be an explicit HTTPS endpoint")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}", self.model_id):
            raise invalid("INVALID_CONFIGURATION: MODEL_ID")
        if (type(self.timeout_seconds) not in (int, float)
                or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120):
            raise invalid("INVALID_CONFIGURATION: MODEL_TIMEOUT_SECONDS")
        if type(self.max_output_tokens) is not int or not 100 <= self.max_output_tokens <= 8000:
            raise invalid("INVALID_CONFIGURATION: MODEL_MAX_OUTPUT_TOKENS")

    @classmethod
    def from_env(cls):
        key = os.environ.get("MODEL_API_KEY", "").strip()
        base = os.environ.get("MODEL_BASE_URL", "").strip()
        model = os.environ.get("MODEL_ID", "").strip()
        if not key or not base or not model:
            raise CoreFailure(ErrorCode.PROVIDER_UNAVAILABLE,
                              "UNCONFIGURED: MODEL_API_KEY, MODEL_BASE_URL and MODEL_ID are required")
        try:
            timeout = float(os.environ.get("MODEL_TIMEOUT_SECONDS", "30"))
        except ValueError:
            raise invalid("INVALID_CONFIGURATION: MODEL_TIMEOUT_SECONDS") from None
        return cls(
            api_key=key, base_url=base, model_id=model,
            enabled=os.environ.get("CAMPUSOPS_LLM_ENABLED", "false").lower() == "true",
            timeout_seconds=timeout,
            max_output_tokens=_integer("MODEL_MAX_OUTPUT_TOKENS", 1600, 100, 8000),
            limits=CoreSettings.from_env(),
            thinking=os.environ.get("MODEL_THINKING", "default"),
        )
