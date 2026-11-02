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

A caller that wants the reply while it is still being written passes a
:class:`TextListener`: it receives the raw reply text as it arrives, and is
told to start over when that text stops counting (a re-ask, or a fallback
model taking over).

A caller whose requests share a growing prefix passes ``cache=True``. The
system prompt and every message except the last are then marked for prompt
caching, so the next request reads them from cache instead of paying for
them again (see :func:`_prompt`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

import anthropic
import httpx2
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
    model: str  # the model that actually served the reply
    request_id: str | None = None
    attempts: int = 1
    fell_back: bool = False  # True when a fallback model served it, not the configured one


@dataclass(frozen=True)
class LLMRequest:
    """What a caller asked for. Recorded by the fake for test assertions."""

    system: str
    messages: Sequence[BetaMessageParam]
    schema: type[BaseModel]
    effort: Effort
    max_tokens: int = DEFAULT_MAX_TOKENS
    cache: bool = False


class TextListener(Protocol):
    """Receives a reply's raw text while the model writes it."""

    def write(self, text: str) -> None:
        """The next piece of the reply."""

    def restart(self) -> None:
        """Everything written so far is void: the reply starts again from the beginning."""


class StructuredLLM(Protocol):
    def generate(
        self,
        *,
        system: str,
        messages: Sequence[BetaMessageParam],
        schema: type[T],
        effort: Effort,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        listener: TextListener | None = None,
        cache: bool = False,
    ) -> LLMResult[T]: ...


class AnthropicLLM:
    """:class:`StructuredLLM` backed by the Anthropic Messages API."""

    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self._model = settings.model
        self._settings = settings
        self._client = client or _client(settings)

    def use_api_key(self, key: str | None) -> None:
        """Send ``key`` with every request from now on.

        None goes back to the SDK's own lookup (``ANTHROPIC_API_KEY`` and so
        on). A request already under way finishes with the key it started with.
        """
        self._client = _client(self._settings, key)

    def generate(
        self,
        *,
        system: str,
        messages: Sequence[BetaMessageParam],
        schema: type[T],
        effort: Effort,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        listener: TextListener | None = None,
        cache: bool = False,
    ) -> LLMResult[T]:
        output_format = {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
        system_param, message_params = _prompt(system, messages, cache)
        usage = Usage()
        problem = "no attempts made"

        for attempt in range(1, REPLY_ATTEMPTS + 1):
            if listener is not None and attempt > 1:
                listener.restart()
            message, request_id = self._call(
                system_param, message_params, output_format, effort, max_tokens, listener
            )
            reply_usage = _usage(message)
            usage += reply_usage
            fell_back = _fell_back(message)
            log.info(
                "llm reply schema=%s attempt=%d stop=%s model=%s fallback=%s request_id=%s "
                "in=%d cache_read=%d cache_write=%d out=%d",
                schema.__name__,
                attempt,
                message.stop_reason,
                message.model,
                fell_back,
                request_id,
                reply_usage.input_tokens,
                reply_usage.cache_read_input_tokens,
                reply_usage.cache_creation_input_tokens,
                reply_usage.output_tokens,
            )

            if message.stop_reason == "refusal":
                category = getattr(message.stop_details, "category", None)
                raise LLMRefusalError(f"the model declined the request (category: {category})")

            if message.stop_reason == "max_tokens":
                problem = f"reply was cut off at max_tokens={max_tokens}"
                max_tokens = min(max_tokens * 2, MAX_TOKENS_CEILING)
                continue

            text = _reply_text(message)
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

            return LLMResult(output, usage, message.model, request_id, attempt, fell_back)

        raise LLMResponseError(
            f"no valid {schema.__name__} after {REPLY_ATTEMPTS} attempts: {problem}"
        )

    def _call(
        self,
        system: str | list[dict[str, Any]],
        messages: list[BetaMessageParam],
        output_format: dict[str, Any],
        effort: Effort,
        max_tokens: int,
        listener: TextListener | None,
    ) -> tuple[Any, str | None]:
        """Return the final message and its request id (read from the stream)."""
        try:
            # Streaming avoids HTTP timeouts on long replies, and lets a
            # listener see the text as it is written.
            with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config={"effort": effort, "format": output_format},
                **self._fallback_params(),
            ) as stream:
                if listener is not None:
                    _relay(stream, listener)
                return stream.get_final_message(), stream.request_id
        except TypeError as exc:
            # With no credentials at all, the SDK raises a plain TypeError when it
            # builds the request headers.
            if "authentication method" not in str(exc):
                raise
            raise LLMConfigError(_credentials_hint(exc)) from exc
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
        except httpx2.TransportError as exc:
            # The SDK wraps errors opening a request, but not a connection that
            # drops while the reply is streaming in.
            raise LLMUnavailableError(
                "the connection to the Anthropic API dropped mid-reply; try again"
            ) from exc

    def _fallback_params(self) -> dict[str, Any]:
        if self._model not in FALLBACK_MODELS:
            return {}
        return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


def _client(settings: Settings, api_key: str | None = None, **options: Any) -> anthropic.Anthropic:
    try:
        return anthropic.Anthropic(
            api_key=api_key,
            max_retries=options.get("max_retries", settings.max_retries),
            timeout=options.get("timeout", settings.timeout),
        )
    except anthropic.AnthropicError as exc:
        raise LLMConfigError(_credentials_hint(exc)) from exc


KEY_CHECK_TIMEOUT = 20.0


def check_api_key(settings: Settings, key: str) -> None:
    """Raise unless ``key`` works with the configured model.

    Looks the model up in the Models API, which needs a valid key but uses
    no tokens. :class:`LLMConfigError` means the key (or the model) is the
    problem; :class:`LLMUnavailableError` means the API couldn't be asked.
    """
    client = _client(settings, key, max_retries=1, timeout=KEY_CHECK_TIMEOUT)
    try:
        client.models.retrieve(settings.model)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise LLMConfigError(
            "Anthropic didn't accept that key. Check that you copied all of it."
        ) from exc
    except anthropic.NotFoundError as exc:
        raise LLMConfigError(
            f"That key works, but it can't use the model {settings.model!r}. Set "
            "INCIDENTLY_MODEL to a model your account has access to."
        ) from exc
    except anthropic.APIStatusError as exc:
        raise LLMUnavailableError(
            f"Anthropic couldn't check the key right now (HTTP {exc.status_code}). Try again."
        ) from exc
    except (anthropic.APIConnectionError, httpx2.TransportError) as exc:
        raise LLMUnavailableError(
            "Couldn't reach Anthropic to check the key. Check your connection."
        ) from exc


def create_llm(settings: Settings) -> StructuredLLM:
    """Return the real client, or the fake one when ``INCIDENTLY_FAKE_LLM`` is set."""
    if settings.fake_llm:
        from .fake_llm import FakeLLM
        from .offline import offline_reply

        return FakeLLM(default=offline_reply)
    return AnthropicLLM(settings)


CACHE_MARKER = {"type": "ephemeral"}  # 5-minute TTL; turns come well within it


def _prompt(
    system: str, messages: Sequence[BetaMessageParam], cache: bool
) -> tuple[str | list[dict[str, Any]], list[BetaMessageParam]]:
    """The ``system`` and ``messages`` request parameters, with cache markers if asked.

    Caching is a prefix match, so the markers go where the prefix stops being
    shared with the next request: after the system prompt, and after the
    second-to-last message. The last message (the newest command and its
    state snapshot) changes every turn and is left unmarked.

    Every message is sent in block form, marked or not. A message's content
    then renders the same way whether or not it carries this turn's marker,
    so the prefix stays byte-identical from one request to the next.
    """
    if not cache:
        return system, list(messages)
    blocks = [_as_blocks(m) for m in messages]
    if len(blocks) >= 2:
        content = blocks[-2]["content"]
        content[-1] = {**content[-1], "cache_control": CACHE_MARKER}
    return [{"type": "text", "text": system, "cache_control": CACHE_MARKER}], blocks


def _as_blocks(message: BetaMessageParam) -> BetaMessageParam:
    content = message["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    return {**message, "content": [dict(block) for block in content]}  # type: ignore[misc]


def _relay(stream: Any, listener: TextListener) -> None:
    """Pass the stream's text to ``listener`` as it arrives.

    A ``fallback`` block means the model writing so far declined and another
    one starts over, so the listener restarts there (see :func:`_reply_text`).
    """
    for event in stream:
        if event.type == "text":
            listener.write(event.text)
        elif event.type == "content_block_start" and event.content_block.type == "fallback":
            listener.restart()


def _usage(message: Any) -> Usage:
    u = message.usage
    return Usage(
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", None) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", None) or 0,
    )


def _fell_back(message: Any) -> bool:
    """Whether a fallback model served the reply instead of the requested one.

    The API signals this with a ``fallback_message`` entry in
    ``usage.iterations``. A ``fallback`` content block alone is not enough: it
    only appears when the requested model started answering and then declined.
    """
    iterations = getattr(message.usage, "iterations", None) or []
    return any(getattr(i, "type", None) == "fallback_message" for i in iterations)


def _reply_text(message: Any) -> str:
    """The text of the final answer, without output from a model that declined.

    A ``fallback`` block marks where a declining model's partial output ends
    and the fallback model's answer begins, so only text after the last one
    counts.
    """
    parts: list[str] = []
    for block in message.content:
        if block.type == "fallback":
            parts.clear()
        elif block.type == "text":
            parts.append(block.text)
    return "".join(parts).strip()


def _credentials_hint(exc: Exception) -> str:
    return (
        "Anthropic credentials are missing or were rejected. Enter a working key on the API key "
        "page (/setup), or put ANTHROPIC_API_KEY in your .env file, or set INCIDENTLY_FAKE_LLM=1 "
        f"to run offline. ({exc})"
    )
