"""Decoding a command's stdout from the model's reply while it streams in."""

import json
import random

import pytest

from incidently.replies import CommandResult, StateUpdates
from incidently.streaming import REPLACEMENT, StdoutDecoder, StdoutRelay


def decode(text, size):
    decoder = StdoutDecoder()
    out = "".join(decoder.feed(text[i : i + size]) for i in range(0, len(text), size))
    return out, decoder


def reply_json(stdout, *, ensure_ascii=True, separators=(",", ":")):
    return json.dumps(
        {"stdout": stdout, "stderr": "SECRET", "exit_code": 0},
        ensure_ascii=ensure_ascii,
        separators=separators,
    )


SAMPLES = [
    "",
    "plain output\n",
    'quotes " and \\ backslashes / slashes',
    "tab\tnewline\ncarriage\rbackspace\bform\f",
    "control \x00 \x01 \x1f characters",
    "a literal \\u0041 is not an escape",
    "snow ☃, accents é, emoji \U0001f600\U0001f680",
    "\x1b[31mANSI colour\x1b[0m",
]


@pytest.mark.parametrize("stdout", SAMPLES)
@pytest.mark.parametrize("ensure_ascii", [True, False])
@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 1000])
def test_decodes_stdout_however_the_reply_is_split(stdout, ensure_ascii, size):
    out, decoder = decode(reply_json(stdout, ensure_ascii=ensure_ascii), size)
    assert out == stdout
    assert decoder.done


def test_random_replies_round_trip():
    rng = random.Random(6)
    alphabet = 'ab "\\\n\t/é☃\U0001f600\x01'
    for _ in range(300):
        stdout = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        text = reply_json(stdout, ensure_ascii=rng.random() < 0.5)
        assert decode(text, rng.randint(1, 9))[0] == stdout


def test_whitespace_between_tokens_is_allowed():
    text = ' \n{ "stdout" :\t"hi", "exit_code": 0}'
    assert decode(text, 1)[0] == "hi"


def test_matches_what_pydantic_validates():
    result = CommandResult(
        stdout='Get-Service "Dhcp"\r\n✓ ok \U0001f600',
        stderr="",
        exit_code=0,
        state_updates=StateUpdates(
            cwd="", user="", connect_to_host="", cli_mode="", services=[], new_facts=[]
        ),
    )
    text = result.model_dump_json()
    assert decode(text, 3)[0] == CommandResult.model_validate_json(text).stdout


def test_nothing_after_stdout_is_decoded():
    decoder = StdoutDecoder()
    assert decoder.feed('{"stdout": "out", "stderr": "SECRET"') == "out"
    assert decoder.done
    assert decoder.feed(', "more": "SECRET"}') == ""


def test_a_reply_not_opening_with_stdout_decodes_nothing():
    out, decoder = decode('{"stderr": "x", "stdout": "y"}', 4)
    assert out == ""
    assert decoder.done


def test_waits_for_the_rest_of_a_split_escape():
    decoder = StdoutDecoder()
    assert decoder.feed('{"stdout": "a\\') == "a"
    assert decoder.feed("u26") == ""
    assert decoder.feed('03b"') == "☃b"


def test_surrogate_pair_split_across_pieces_is_one_character():
    decoder = StdoutDecoder()
    assert decoder.feed('{"stdout": "\\ud83d') == ""
    assert decoder.feed("\\ude00") == "\U0001f600"


@pytest.mark.parametrize(
    "escaped",
    [
        "\\ud800x",  # high surrogate, then an ordinary character
        "\\ud800\\n",  # high surrogate, then a different escape
        "\\ud800\\u0041",  # high surrogate, then a non-surrogate \u escape
        "\\udc00x",  # low surrogate on its own
    ],
)
def test_lone_surrogates_become_the_replacement_character(escaped):
    out, _ = decode('{"stdout": "' + escaped + '"}', 1)
    assert out[0] == REPLACEMENT
    assert out.encode("utf-8")  # safe to send to the browser


def test_malformed_escape_becomes_the_replacement_character():
    assert decode('{"stdout": "a\\qb"}', 1)[0] == f"a{REPLACEMENT}b"


# --- relay --------------------------------------------------------------------


class Output:
    def __init__(self):
        self.events = []

    def write(self, text):
        self.events.append(("write", text))

    def restart(self):
        self.events.append(("restart", None))


def test_relay_forwards_decoded_output_only():
    output = Output()
    relay = StdoutRelay(output)
    for piece in ['{"std', 'out": "he', 'llo", "new_facts": ["SECRET"]}']:
        relay.write(piece)
    assert output.events == [("write", "he"), ("write", "llo")]


def test_relay_restarts_only_after_sending_output():
    output = Output()
    relay = StdoutRelay(output)

    relay.write('{"stdou')
    relay.restart()  # nothing was shown yet: nothing to take back
    relay.write('{"stdout": "first')
    relay.restart()
    relay.write('{"stdout": "second"}')

    assert output.events == [("write", "first"), ("restart", None), ("write", "second")]
