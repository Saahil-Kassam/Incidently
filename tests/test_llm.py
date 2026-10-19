"""AnthropicLLM against a stub of the SDK client. No network access."""

from collections import deque
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, Field

from incidently.config import Settings
from incidently.fake_llm import FakeLLM
from incidently.llm import (
    FALLBACK_BETA,
    AnthropicLLM,
    LLMConfigError,
    LLMRefusalError,
    LLMResponseError,
    LLMUnavailableError,
    Usage,
    create_llm,
)
from incidently.replies import CommandResult


class Reply(BaseModel):
    stdout: str
    exit_code: int = Field(ge=0)


def message(
    text="",
    stop_reason="end_turn",
    model="claude-opus-5-5",
    category=None,
    content=None,
    iterations=None,
):
    if content is None:
        content = [SimpleNamespace(type="text", text=text)] if text else []
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if category else None,
        model=model,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=50,
            cache_creation_input_tokens=None,
            iterations=iterations,
        ),
    )


class StubClient:
    """Mimics ``client.beta.messages.stream(...)`` returning a context manager."""

    def __init__(self, *results):
        self.results = deque(results)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results.popleft()
        if isinstance(result, Exception):
            raise result
        return _Stream(result)


class _Stream:
    # The real SDK exposes the request id on the stream (from the response
    # headers), not on the final message.
    request_id = "req_123"

    def __init__(self, result):
        self._result = result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._result


def make_llm(*results, model="claude-opus-5-5"):
    client = StubClient(*results)
    llm = AnthropicLLM(Settings(model=model), client=client)
    return llm, client


def generate(llm, **overrides):
    kwargs = {
        "system": "You simulate a Linux host.",
        "messages": [{"role": "user", "content": "uptime"}],
        "schema": Reply,
        "effort": "low",
    }
    return llm.generate(**(kwargs | overrides))


def api_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


# --- successful replies -----------------------------------------------------


def test_returns_validated_output_and_metadata():
    llm, _ = make_llm(message('{"stdout": "up 3 days", "exit_code": 0}'))
    result = generate(llm)

    assert result.output == Reply(stdout="up 3 days", exit_code=0)
    assert result.model == "claude-opus-5-5"
    assert result.request_id == "req_123"
    assert result.attempts == 1
    assert result.usage == Usage(100, 20, 50, 0)


def test_request_shape():
    llm, client = make_llm(message('{"stdout": "", "exit_code": 0}'))
    generate(llm, effort="high", max_tokens=8000)

    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 8000
    assert call["system"] == "You simulate a Linux host."
    assert call["messages"] == [{"role": "user", "content": "uptime"}]
    assert call["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": anthropic.transform_schema(Reply)},
    }
    assert call["betas"] == [FALLBACK_BETA]
    assert call["fallbacks"] == "default"


def test_no_fallback_params_for_models_without_server_side_fallback():
    llm, client = make_llm(message('{"stdout": "", "exit_code": 0}'), model="claude-haiku-5-5")
    generate(llm)
    assert "fallbacks" not in client.calls[0]
    assert "betas" not in client.calls[0]


def test_no_fallback_reported_when_the_requested_model_served():
    iterations = [SimpleNamespace(type="message")]
    llm, _ = make_llm(message('{"stdout": "", "exit_code": 0}', iterations=iterations))
    result = generate(llm)
    assert result.fell_back is False
    assert result.model == "claude-opus-5-5"


def test_fallback_is_reported_with_the_serving_model():
    # What the live API returned in the Milestone 2 smoke test: no fallback
    # block, just a fallback_message iteration naming the serving model.
    iterations = [SimpleNamespace(type="fallback_message", model="claude-opus-4-8")]
    llm, _ = make_llm(
        message('{"stdout": "", "exit_code": 0}', model="claude-opus-4-8", iterations=iterations)
    )
    result = generate(llm)
    assert result.fell_back is True
    assert result.model == "claude-opus-4-8"


def test_text_before_a_fallback_block_is_discarded():
    content = [
        SimpleNamespace(type="text", text='{"stdout": "partial'),
        SimpleNamespace(type="fallback"),
        SimpleNamespace(type="text", text='{"stdout": "from fallback", "exit_code": 0}'),
    ]
    llm, _ = make_llm(message(content=content))
    assert generate(llm).output.stdout == "from fallback"


# --- re-asking on bad replies -----------------------------------------------


def test_truncated_reply_is_retried_with_more_tokens():
    llm, client = make_llm(
        message('{"stdout": "lo', stop_reason="max_tokens"),
        message('{"stdout": "long", "exit_code": 0}'),
    )
    result = generate(llm, max_tokens=1000)

    assert result.output.stdout == "long"
    assert result.attempts == 2
    assert [c["max_tokens"] for c in client.calls] == [1000, 2000]
    assert result.usage.input_tokens == 200  # usage from both attempts


def test_max_tokens_growth_is_capped():
    llm, client = make_llm(
        message("x", stop_reason="max_tokens"),
        message("x", stop_reason="max_tokens"),
        message("x", stop_reason="max_tokens"),
    )
    with pytest.raises(LLMResponseError, match="cut off"):
        generate(llm, max_tokens=40_000)
    assert [c["max_tokens"] for c in client.calls] == [40_000, 64_000, 64_000]


def test_invalid_reply_is_retried():
    llm, client = make_llm(
        message('{"stdout": "x", "exit_code": -1}'),  # violates ge=0
        message("not json"),
        message('{"stdout": "ok", "exit_code": 0}'),
    )
    result = generate(llm)
    assert result.output.stdout == "ok"
    assert result.attempts == 3
    assert len(client.calls) == 3


def test_empty_reply_is_retried():
    llm, _ = make_llm(message(""), message('{"stdout": "ok", "exit_code": 0}'))
    assert generate(llm).attempts == 2


def test_gives_up_after_three_bad_replies():
    llm, client = make_llm(message("bad"), message("bad"), message("bad"))
    with pytest.raises(LLMResponseError, match="no valid Reply after 3 attempts"):
        generate(llm)
    assert len(client.calls) == 3


def test_refusal_is_not_retried():
    llm, client = make_llm(message(stop_reason="refusal", category="cyber"))
    with pytest.raises(LLMRefusalError, match="cyber"):
        generate(llm)
    assert len(client.calls) == 1


# --- API errors ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected", "match"),
    [
        (api_error(anthropic.AuthenticationError, 401), LLMConfigError, "ANTHROPIC_API_KEY"),
        (api_error(anthropic.PermissionDeniedError, 403), LLMConfigError, "ANTHROPIC_API_KEY"),
        (api_error(anthropic.NotFoundError, 404), LLMConfigError, "was not found"),
        (api_error(anthropic.BadRequestError, 400), LLMResponseError, "rejected the request"),
        (api_error(anthropic.RateLimitError, 429), LLMUnavailableError, "HTTP 429"),
        (api_error(anthropic.InternalServerError, 500), LLMUnavailableError, "HTTP 500"),
        (api_error(anthropic.APIStatusError, 529), LLMUnavailableError, "HTTP 529"),
        (
            anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            ),
            LLMUnavailableError,
            "could not reach",
        ),
    ],
)
def test_api_errors_are_translated(error, expected, match):
    llm, client = make_llm(error)
    with pytest.raises(expected, match=match):
        generate(llm)
    assert len(client.calls) == 1  # SDK already retried; we don't retry again


def test_missing_credentials_are_a_config_error():
    # Raised by the SDK while building headers when no key is configured.
    llm, _ = make_llm(TypeError('"Could not resolve authentication method. Expected ..."'))
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        generate(llm)


def test_unrelated_type_errors_are_not_hidden():
    llm, _ = make_llm(TypeError("unexpected keyword argument 'fallbacks'"))
    with pytest.raises(TypeError, match="fallbacks"):
        generate(llm)


# --- factory ----------------------------------------------------------------


def test_create_llm_returns_offline_fake_when_configured():
    llm = create_llm(Settings(fake_llm=True))
    assert isinstance(llm, FakeLLM)
    # Offline play never runs out of replies.
    assert generate(llm, schema=CommandResult, messages=[{"role": "user", "content": "x"}])


def test_create_llm_returns_real_client_with_sdk_retry_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    llm = create_llm(Settings(max_retries=7, timeout=12.0))
    assert isinstance(llm, AnthropicLLM)
    assert llm._client.max_retries == 7
    assert llm._client.timeout == 12.0
