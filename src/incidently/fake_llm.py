"""A scripted stand-in for the language model.

:class:`FakeLLM` satisfies :class:`incidently.llm.StructuredLLM` without any
network access, so tests and offline development never spend API credits.
Queue the replies a test expects, in order; each one can be a ready-made
model instance, an exception to raise, or a function that builds the reply
from the request. Every request is recorded in :attr:`FakeLLM.requests`.

Given a listener, the fake streams the reply's JSON to it in small pieces,
the way the real model does, so streaming code paths run offline too.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from typing import TypeAlias, TypeVar

from anthropic.types.beta import BetaMessageParam
from pydantic import BaseModel

from .config import Effort
from .llm import (
    DEFAULT_MAX_TOKENS,
    LLMRequest,
    LLMResponseError,
    LLMResult,
    TextListener,
    Usage,
)

T = TypeVar("T", bound=BaseModel)

Reply: TypeAlias = BaseModel | Exception | Callable[[LLMRequest], BaseModel]

FAKE_MODEL = "fake-llm"
# Odd-sized, so escape sequences regularly straddle two pieces.
STREAM_CHUNK = 7


class FakeLLM:
    def __init__(
        self,
        *replies: Reply,
        default: Callable[[LLMRequest], BaseModel] | None = None,
    ) -> None:
        """``default`` answers any request once the queued replies run out."""
        self._replies: deque[Reply] = deque(replies)
        self._default = default
        self.requests: list[LLMRequest] = []

    def queue(self, *replies: Reply) -> None:
        self._replies.extend(replies)

    @property
    def pending(self) -> int:
        """Replies queued but not yet used."""
        return len(self._replies)

    def generate(
        self,
        *,
        system: str,
        messages: Sequence[BetaMessageParam],
        schema: type[T],
        effort: Effort,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        listener: TextListener | None = None,
    ) -> LLMResult[T]:
        request = LLMRequest(system, list(messages), schema, effort, max_tokens)
        self.requests.append(request)

        if self._replies:
            reply = self._replies.popleft()
        elif self._default is not None:
            reply = self._default
        else:
            raise LLMResponseError(f"FakeLLM has no scripted reply for {schema.__name__}")
        if isinstance(reply, Exception):
            raise reply
        output = reply if isinstance(reply, BaseModel) else reply(request)
        if not isinstance(output, schema):
            raise TypeError(
                f"FakeLLM scripted a {type(output).__name__} but the caller asked for "
                f"{schema.__name__}"
            )
        if listener is not None:
            text = output.model_dump_json()
            for i in range(0, len(text), STREAM_CHUNK):
                listener.write(text[i : i + STREAM_CHUNK])
        return LLMResult(
            output=output,
            usage=Usage(),
            model=FAKE_MODEL,
            request_id=f"fake-{len(self.requests)}",
        )
