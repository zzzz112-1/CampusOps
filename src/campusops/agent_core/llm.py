"""One explicitly configured OpenAI-compatible endpoint; zero automatic retries."""

import json
import logging
import os

from openai import APIConnectionError, APIStatusError, APITimeoutError, DefaultHttpxClient, OpenAI, RateLimitError

from campusops.agent_core.budget import BudgetLedger
from campusops.agent_core.config import ModelSettings
from campusops.agent_core.errors import CoreFailure
from campusops.agent_core.input_limits import require_bounded_input
from campusops.contracts.models import ErrorCode, ModelBudgetSnapshot, ModelRequest, ModelResponse


def _provider_debug_enabled() -> bool:
    namespaces = ("openai", "httpx", "httpcore", "httpx2", "httpcore2")
    names = set(namespaces)
    # A child logger can be DEBUG even when its parent is WARNING. Inspect
    # existing logger levels without changing application logging configuration.
    names.update(name for name, logger in logging.Logger.manager.loggerDict.copy().items()
                 if isinstance(logger, logging.Logger)
                 and any(name.startswith(prefix + ".") for prefix in namespaces))
    return any(logging.getLogger(name).isEnabledFor(logging.DEBUG) for name in names)


class LLMAdapter:
    def __init__(self, settings: ModelSettings, *, budget: BudgetLedger | None = None):
        self.settings = settings
        self.budget = budget or BudgetLedger()

    @classmethod
    def from_env(cls):
        return cls(ModelSettings.from_env())

    def get_model_budget(self, run_id: str) -> ModelBudgetSnapshot:
        """Local quota reservations only; no model call, reserve or provider query."""
        return self.budget.get_model_budget(run_id, self.settings.limits)

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.settings.enabled or not self.settings.api_key:
            raise CoreFailure(ErrorCode.PROVIDER_UNAVAILABLE,
                              "UNCONFIGURED: real API execution must be explicitly enabled")
        # The SDK may log request bodies at DEBUG. Refuse that configuration rather
        # than changing global logging settings or emitting credentials/prompts.
        if (os.environ.get("OPENAI_LOG", "").lower() == "debug"
                or _provider_debug_enabled()):
            raise CoreFailure(ErrorCode.INVALID_STATE, "UNSAFE_PROVIDER_DEBUG_LOGGING")
        try:
            request = ModelRequest.model_validate(request)
            schema = json.loads(request.response_schema)
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ValueError("Object schema required")
            # Python's JSON decoder accepts NaN/Infinity. HTTP JSON does not;
            # reject them locally before consuming a quota reservation.
            json.dumps(schema, allow_nan=False)
            if not request.messages or any(message.role == "tool" for message in request.messages):
                raise ValueError("Only structured text messages are supported")
            messages = [message.model_dump() for message in request.messages]
            completion = {
                "model": self.settings.model_id, "messages": messages,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "campusops_role_result", "strict": True, "schema": schema,
                }},
                "max_completion_tokens": self.settings.max_output_tokens,
                **({} if self.settings.thinking == "default" else {
                    "extra_body": {"thinking": {"type": self.settings.thinking}},
                }),
            }
            require_bounded_input(completion)
        except CoreFailure:
            raise
        except Exception:
            raise CoreFailure(ErrorCode.INVALID_INPUT, "INVALID_MODEL_REQUEST") from None
        self.budget.reserve(request.run_id, self.settings.limits)
        attempted = False

        def dispatched(_request):
            nonlocal attempted
            # Mark the HTTP send boundary, not entry into SDK request building.
            # No request contents or headers are read or logged by this hook.
            attempted = True

        try:
            with DefaultHttpxClient(timeout=self.settings.timeout_seconds, follow_redirects=False,
                                    event_hooks={"request": [dispatched]}) as transport, OpenAI(
                api_key=self.settings.api_key, base_url=self.settings.base_url,
                timeout=self.settings.timeout_seconds, max_retries=0, http_client=transport,
            ) as client:
                response = client.chat.completions.create(**completion)
            if not response.choices or response.choices[0].finish_reason != "stop":
                raise CoreFailure(ErrorCode.WORKFLOW_FAILED, "MODEL_OUTPUT_INCOMPLETE", attempted=True)
            message = response.choices[0].message
            if message.refusal or not message.content or message.tool_calls:
                raise CoreFailure(ErrorCode.WORKFLOW_FAILED, "MODEL_OUTPUT_REFUSED_OR_INVALID", attempted=True)
            # Role-specific Pydantic validation is performed by the orchestration caller.
            return ModelResponse(
                content=message.content, provider="openai_compatible", model_id=self.settings.model_id,
                input_tokens=response.usage.prompt_tokens if response.usage else None,
                output_tokens=response.usage.completion_tokens if response.usage else None,
            )
        except CoreFailure:
            raise
        except APITimeoutError:
            raise CoreFailure(ErrorCode.PROVIDER_UNAVAILABLE, "MODEL_TIMEOUT", attempted=attempted) from None
        except RateLimitError:
            raise CoreFailure(ErrorCode.BUDGET_EXCEEDED, "PROVIDER_RATE_OR_QUOTA_LIMIT", attempted=attempted) from None
        except APIConnectionError:
            raise CoreFailure(ErrorCode.PROVIDER_UNAVAILABLE, "PROVIDER_REQUEST_FAILED", attempted=attempted) from None
        except APIStatusError as exc:
            # The status is safe diagnostic evidence; never include its body,
            # headers, request URL or original exception text.
            status = exc.status_code
            reason = f"PROVIDER_HTTP_{status}" if type(status) is int and 100 <= status <= 599 else "PROVIDER_REQUEST_FAILED"
            raise CoreFailure(ErrorCode.PROVIDER_UNAVAILABLE, reason, attempted=attempted) from None
        except Exception:
            reason = "PROVIDER_RESPONSE_INVALID" if attempted else "MODEL_REQUEST_BUILD_FAILED"
            raise CoreFailure(ErrorCode.PROVIDER_UNAVAILABLE, reason, attempted=attempted) from None
