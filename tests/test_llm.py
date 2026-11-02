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

    def __iter__(self):
        # The events the SDK yields for the message's content: text arrives in
        # small pieces, and a fallback block only as its start event.
        for block in self._result.content:
            if block.type == "fallback":
                yield SimpleNamespace(type="content_block_start", content_block=block)
            elif block.type == "text":
                yield SimpleNamespace(type="content_block_start", content_block=block)
                for i in range(0, len(block.text), 5):
                    yield SimpleNamespace(type="text", text=block.text[i : i + 5])

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
        (httpx2.ReadTimeout("slow"), LLMUnavailableError, "dropped mid-reply"),
    ],
)
def test_api_errors_are_translated(error, expected, match):
    llm, client = make_llm(error)
    with pytest.raises(expected, match=match):
        generate(llm)
    assert len(client.calls) == 1  # SDK already retried; we don't retry again


def test_connection_dropped_while_streaming():
    class Dropped(_Stream):
        def get_final_message(self):
            raise httpx2.RemoteProtocolError("peer closed connection without sending complete body")

    llm, client = make_llm()
    client.beta.messages.stream = lambda **kwargs: Dropped(None)
    with pytest.raises(LLMUnavailableError, match="dropped mid-reply"):
        generate(llm)


def test_missing_credentials_are_a_config_error():
    # Raised by the SDK while building headers when no key is configured.
    llm, _ = make_llm(TypeError('"Could not resolve authentication method. Expected ..."'))
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        generate(llm)


def test_unrelated_type_errors_are_not_hidden():
    llm, _ = make_llm(TypeError("unexpected keyword argument 'fallbacks'"))
    with pytest.raises(TypeError, match="fallbacks"):
        generate(llm)


# --- prompt caching ----------------------------------------------------------

MARKER = {"type": "ephemeral"}
REPLY_JSON = '{"stdout": "", "exit_code": 0}'


def history(turns):
    """A command conversation: ``turns`` past exchanges, then the newest command."""
    messages = []
    for i in range(turns):
        messages += [
            {"role": "user", "content": f"<command>cmd {i}</command>"},
            {"role": "assistant", "content": f'{{"stdout": "out {i}"}}'},
        ]
    return [*messages, {"role": "user", "content": "<session_state>{}</session_state> newest"}]


def without_markers(messages):
    return [
        {
            **m,
            "content": [{k: v for k, v in b.items() if k != "cache_control"} for b in m["content"]],
        }
        for m in messages
    ]


def test_without_cache_the_request_is_unchanged():
    llm, client = make_llm(message(REPLY_JSON))
    generate(llm, messages=history(1))
    call = client.calls[0]
    assert call["system"] == "You simulate a Linux host."
    assert call["messages"] == history(1)


def test_cache_marks_the_system_prompt_and_the_shared_history():
    llm, client = make_llm(message(REPLY_JSON))
    generate(llm, messages=history(2), cache=True)
    call = client.calls[0]

    assert call["system"] == [
        {"type": "text", "text": "You simulate a Linux host.", "cache_control": MARKER}
    ]
    messages = call["messages"]
    assert all(isinstance(m["content"], list) for m in messages)
    marked = [i for i, m in enumerate(messages) for b in m["content"] if "cache_control" in b]
    # Only the last message before the newest command; the newest changes every turn.
    assert marked == [len(messages) - 2]


def test_a_single_message_caches_only_the_system_prompt():
    llm, client = make_llm(message(REPLY_JSON))
    generate(llm, messages=history(0), cache=True)
    call = client.calls[0]
    assert "cache_control" in call["system"][0]
    assert all("cache_control" not in b for m in call["messages"] for b in m["content"])


def test_cached_prefix_is_byte_identical_from_turn_to_turn():
    llm, client = make_llm(message(REPLY_JSON), message(REPLY_JSON))
    generate(llm, messages=history(2), cache=True)
    generate(llm, messages=history(3), cache=True)
    first, second = (without_markers(c["messages"]) for c in client.calls)
    # Everything the first request marked for caching reappears unchanged in the second.
    assert second[: len(first) - 1] == first[:-1]
    assert client.calls[0]["system"] == client.calls[1]["system"]


def test_caching_does_not_change_the_callers_messages():
    messages = history(1)
    llm, _ = make_llm(message(REPLY_JSON))
    generate(llm, messages=messages, cache=True)
    assert messages == history(1)


def test_cache_usage_is_reported_per_call():
    reply = message(REPLY_JSON)
    reply.usage.cache_read_input_tokens = 1200
    reply.usage.cache_creation_input_tokens = 300
    llm, _ = make_llm(reply)
    assert generate(llm, cache=True).usage == Usage(100, 20, 1200, 300)


# --- streaming to a listener -------------------------------------------------


class Listener:
    """Records what a listener hears; ``restart`` shows up as a marker."""

    def __init__(self):
        self.heard = []

    def write(self, text):
        self.heard.append(text)

    def restart(self):
        self.heard.append(RESTART)

    def text(self):
        return "".join(h for h in self.heard if h is not RESTART)


RESTART = object()


def test_listener_hears_the_reply_as_it_arrives():
    text = '{"stdout": "up 3 days", "exit_code": 0}'
    llm, _ = make_llm(message(text))
    listener = Listener()

    result = generate(llm, listener=listener)

    assert len(listener.heard) > 1
    assert listener.text() == text
    assert result.output.stdout == "up 3 days"


def test_listener_restarts_when_a_reply_is_re_asked():
    llm, _ = make_llm(
        message('{"stdout": "lo', stop_reason="max_tokens"),
        message('{"stdout": "long", "exit_code": 0}'),
    )
    listener = Listener()

    generate(llm, listener=listener)

    restart = listener.heard.index(RESTART)
    assert "".join(listener.heard[:restart]) == '{"stdout": "lo'
    assert "".join(listener.heard[restart + 1 :]) == '{"stdout": "long", "exit_code": 0}'


def test_listener_restarts_when_a_fallback_model_takes_over():
    content = [
        SimpleNamespace(type="text", text='{"stdout": "partial'),
        SimpleNamespace(type="fallback"),
        SimpleNamespace(type="text", text='{"stdout": "from fallback", "exit_code": 0}'),
    ]
    llm, _ = make_llm(message(content=content))
    listener = Listener()

    generate(llm, listener=listener)

    restart = listener.heard.index(RESTART)
    assert "".join(listener.heard[restart + 1 :]) == '{"stdout": "from fallback", "exit_code": 0}'


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
