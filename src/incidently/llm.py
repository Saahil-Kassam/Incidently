"""The boundary between Incidently and the language model.

Everything else in the app talks to the model through the
:class:`StructuredLLM` protocol: send a system prompt and messages, name a
Pydantic model, and get back a validated instance of it. Two implementations
exist:

* :class:`AnthropicLLM`, the only code that calls the Anthropic API.
* :class:`incidently.fake_llm.FakeLLM`, a scripted stand-in for tests and
  offline development.

Every reply is constrained with structured outputs (a JSON schema built from
the Pydantic model) and then validated locally. Transient API failures are
retried by the Anthropic SDK itself; this module adds a second, smaller loop
that re-asks when a reply is empty, cut off, or fails validation.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

import anthropic
from anthropic.types.beta import BetaMessageParam
from pydantic import BaseModel, ValidationError

from .config import Effort, Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Server-side refusal fallback: if a safety classifier declines a request, the
# API re-runs it on a fallback model it chooses, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)

DEFAULT_MAX_TOKENS = 16_000
MAX_TOKENS_CEILING = 64_000
REPLY_ATTEMPTS = 3


class LLMError(Exception):
    """Base class for every model-related failure."""


class LLMConfigError(LLMError):
    """Credentials are missing or rejected. Retrying will not help."""


class LLMUnavailableError(LLMError):
    """The API could not be reached or stayed overloaded after retries."""


class LLMRefusalError(LLMError):
    """The model declined the request, including any fallback model."""


class LLMResponseError(LLMError):
    """No valid reply after several attempts, or the request was malformed."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    output: T
    usage: Usage
    model: str
    request_id: str | None = None
    attempts: int = 1


@dataclass(frozen=True)
class LLMRequest:
    """What a caller asked for. Recorded by the fake for test assertions."""

    system: str
    messages: Sequence[BetaMessageParam]
    schema: type[BaseModel]
    effort: Effort
    max_tokens: int = DEFAULT_MAX_TOKENS


class StructuredLLM(Protocol):
    def generate(
        self,
        *,
        system: str,
        messages: Sequence[BetaMessageParam],
        schema: type[T],
        effort: Effort,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResult[T]: ...


class AnthropicLLM:
    """:class:`StructuredLLM` backed by the Anthropic Messages API."""

    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self._model = settings.model
        try:
            self._client = client or anthropic.Anthropic(
                max_retries=settings.max_retries, timeout=settings.timeout
            )
        except anthropic.AnthropicError as exc:
            raise LLMConfigError(_credentials_hint(exc)) from exc

    def generate(
        self,
        *,
        system: str,
        messages: Sequence[BetaMessageParam],
        schema: type[T],
        effort: Effort,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResult[T]:
        output_format = {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
        usage = Usage()
        problem = "no attempts made"

        for attempt in range(1, REPLY_ATTEMPTS + 1):
            message = self._call(system, messages, output_format, effort, max_tokens)
            usage += _usage(message)
            request_id = getattr(message, "_request_id", None)
            log.info(
                "llm reply schema=%s attempt=%d stop=%s model=%s request_id=%s in=%d out=%d",
                schema.__name__,
                attempt,
                message.stop_reason,
                message.model,
                request_id,
                message.usage.input_tokens,
                message.usage.output_tokens,
            )

            if message.stop_reason == "refusal":
                category = getattr(message.stop_details, "category", None)
                raise LLMRefusalError(f"the model declined the request (category: {category})")

            if message.stop_reason == "max_tokens":
                problem = f"reply was cut off at max_tokens={max_tokens}"
                max_tokens = min(max_tokens * 2, MAX_TOKENS_CEILING)
                continue

            text = "".join(b.text for b in message.content if b.type == "text").strip()
            if not text:
                problem = "reply contained no text"
                continue
            try:
                output = schema.model_validate_json(text)
            except ValidationError as exc:
                problem = f"reply failed validation: {exc.error_count()} error(s)"
                log.warning(
                    "invalid %s reply (request_id=%s): %s", schema.__name__, request_id, exc
                )
                continue

            return LLMResult(output, usage, message.model, request_id, attempt)

        raise LLMResponseError(
            f"no valid {schema.__name__} after {REPLY_ATTEMPTS} attempts: {problem}"
        )

    def _call(
        self,
        system: str,
        messages: Sequence[BetaMessageParam],
        output_format: dict[str, Any],
        effort: Effort,
        max_tokens: int,
    ) -> Any:
        try:
            # Streaming avoids HTTP timeouts on long replies; we only need the
            # final message here, so the stream is drained by the SDK helper.
            with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=list(messages),
                output_config={"effort": effort, "format": output_format},
                **self._fallback_params(),
            ) as stream:
                return stream.get_final_message()
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.CredentialsError,
        ) as exc:
            raise LLMConfigError(_credentials_hint(exc)) from exc
        except anthropic.NotFoundError as exc:
            raise LLMConfigError(f"model {self._model!r} was not found: {exc.message}") from exc
        except anthropic.BadRequestError as exc:
            raise LLMResponseError(f"the API rejected the request: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            # Rate limits and 5xx: the SDK has already retried these with backoff.
            raise LLMUnavailableError(
                f"the API is unavailable (HTTP {exc.status_code}); try again shortly"
            ) from exc
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise LLMUnavailableError(
                "could not reach the Anthropic API; check your connection"
            ) from exc

    def _fallback_params(self) -> dict[str, Any]:
        if self._model not in FALLBACK_MODELS:
            return {}
        return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


def create_llm(settings: Settings) -> StructuredLLM:
    """Return the real client, or the fake one when ``INCIDENTLY_FAKE_LLM`` is set."""
    if settings.fake_llm:
        from .fake_llm import FakeLLM

        return FakeLLM()
    return AnthropicLLM(settings)


def _usage(message: Any) -> Usage:
    u = message.usage
    return Usage(
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", None) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", None) or 0,
    )


def _credentials_hint(exc: Exception) -> str:
    return (
        "Anthropic credentials are missing or were rejected. Put ANTHROPIC_API_KEY in your "
        f".env file (see .env.example), or set INCIDENTLY_FAKE_LLM=1 to run offline. ({exc})"
    )
