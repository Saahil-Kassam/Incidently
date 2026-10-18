import pytest
from pydantic import BaseModel

from incidently.fake_llm import FAKE_MODEL, FakeLLM
from incidently.llm import LLMResponseError, LLMUnavailableError


class Reply(BaseModel):
    stdout: str


class Other(BaseModel):
    value: int


def generate(llm, schema=Reply, content="ls"):
    return llm.generate(
        system="sys",
        messages=[{"role": "user", "content": content}],
        schema=schema,
        effort="low",
    )


def test_returns_queued_replies_in_order():
    llm = FakeLLM(Reply(stdout="one"))
    llm.queue(Reply(stdout="two"))

    assert generate(llm).output.stdout == "one"
    assert generate(llm).output.stdout == "two"
    assert llm.pending == 0


def test_result_metadata():
    result = generate(FakeLLM(Reply(stdout="x")))
    assert result.model == FAKE_MODEL
    assert result.request_id == "fake-1"
    assert result.usage.input_tokens == 0


def test_records_requests():
    llm = FakeLLM(Reply(stdout="x"))
    generate(llm, content="whoami")

    (request,) = llm.requests
    assert request.system == "sys"
    assert request.messages == [{"role": "user", "content": "whoami"}]
    assert request.schema is Reply
    assert request.effort == "low"


def test_callable_reply_sees_the_request():
    llm = FakeLLM(lambda req: Reply(stdout=f"ran: {req.messages[-1]['content']}"))
    assert generate(llm, content="uptime").output.stdout == "ran: uptime"


def test_scripted_exception_is_raised():
    llm = FakeLLM(LLMUnavailableError("down"))
    with pytest.raises(LLMUnavailableError, match="down"):
        generate(llm)


def test_empty_queue_raises():
    with pytest.raises(LLMResponseError, match="no scripted reply for Reply"):
        generate(FakeLLM())


def test_wrong_schema_is_a_test_bug():
    llm = FakeLLM(Other(value=1))
    with pytest.raises(TypeError, match="scripted a Other but the caller asked for Reply"):
        generate(llm)
