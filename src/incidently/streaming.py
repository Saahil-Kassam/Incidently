"""Showing a command's output while the model is still writing it.

A simulated command's reply is a JSON object whose first field is
``stdout`` (see :class:`incidently.replies.CommandResult`). :class:`StdoutDecoder`
reads that object a piece at a time and returns the decoded ``stdout`` text
as soon as it arrives, without waiting for the rest of the reply.

It stops at the end of the ``stdout`` string, so nothing after it (the state
updates and new facts, which can mention hidden details) is ever decoded. If
the reply doesn't open with ``stdout``, it decodes nothing and the caller
shows the output once the whole reply is in.

:class:`StdoutRelay` connects the two ends: it is the
:class:`incidently.llm.TextListener` given to the model call, and passes the
decoded text on to another listener (the web app's response stream).
"""

from __future__ import annotations

import json
from enum import Enum

from .llm import TextListener

# The opening of the reply, token by token; JSON allows whitespace between tokens.
_OPENING = ("{", '"stdout"', ":", '"')
_WHITESPACE = frozenset(" \t\r\n")
REPLACEMENT = chr(0xFFFD)  # stands in for anything that can't be decoded


class _State(Enum):
    OPENING = "opening"
    STRING = "string"
    DONE = "done"


class StdoutDecoder:
    """Decodes the ``stdout`` string from a JSON reply fed to it in pieces."""

    def __init__(self) -> None:
        self._state = _State.OPENING
        self._token = 0  # index into _OPENING
        self._matched = 0  # characters of that token matched so far
        self._pending = ""  # an escape sequence split across pieces

    @property
    def done(self) -> bool:
        """True once the string has ended, or the reply turned out not to start with it."""
        return self._state is _State.DONE

    def feed(self, text: str) -> str:
        """Take the next piece of the reply and return the newly decoded output."""
        if self._state is _State.OPENING:
            text = self._open(text)
        if self._state is not _State.STRING:
            return ""
        return self._decode(self._pending + text)

    def _open(self, text: str) -> str:
        """Match the reply's opening; return what follows it."""
        for i, char in enumerate(text):
            token = _OPENING[self._token]
            if self._matched == 0 and char in _WHITESPACE:
                continue
            if char != token[self._matched]:
                self._state = _State.DONE
                return ""
            self._matched += 1
            if self._matched == len(token):
                self._token += 1
                self._matched = 0
                if self._token == len(_OPENING):
                    self._state = _State.STRING
                    return text[i + 1 :]
        return ""

    def _decode(self, data: str) -> str:
        out: list[str] = []
        i = 0
        while i < len(data):
            char = data[i]
            if char == '"':
                self._state = _State.DONE
                self._pending = ""
                return "".join(out)
            if char != "\\":
                end = _next_special(data, i)
                out.append(data[i:end])
                i = end
                continue
            end = _escape_end(data, i)
            if end is None:
                break  # incomplete: wait for the next piece
            out.append(_unescape(data[i:end]))
            i = end
        self._pending = data[i:]
        return "".join(out)


class StdoutRelay:
    """A :class:`TextListener` for the model call that forwards only decoded output.

    ``restart`` is forwarded only when output was already passed on, since
    there is nothing to take back otherwise.
    """

    def __init__(self, output: TextListener) -> None:
        self._output = output
        self._decoder = StdoutDecoder()
        self._sent = False

    def write(self, text: str) -> None:
        decoded = self._decoder.feed(text)
        if decoded:
            self._sent = True
            self._output.write(decoded)

    def restart(self) -> None:
        self._decoder = StdoutDecoder()
        if self._sent:
            self._sent = False
            self._output.restart()


def _next_special(data: str, start: int) -> int:
    """Index of the next quote or backslash at or after ``start``, else the end."""
    ends = [i for i in (data.find('"', start), data.find("\\", start)) if i != -1]
    return min(ends, default=len(data))


def _escape_end(data: str, i: int) -> int | None:
    """End of the escape sequence starting at ``data[i]``, or None if it isn't all here.

    A ``\\uXXXX`` high surrogate is read together with the low surrogate that
    should follow it, so the pair decodes to one character.
    """
    n = len(data)
    if i + 2 > n:
        return None
    if data[i + 1] != "u":
        return i + 2
    if i + 6 > n:
        return None
    if not _is_high_surrogate(data[i + 2 : i + 6]):
        return i + 6
    if i + 7 > n or (data[i + 6] == "\\" and i + 8 > n):
        return None
    if data[i + 6 : i + 8] != "\\u":
        return i + 6
    if i + 12 > n:
        return None
    return i + 12 if _is_low_surrogate(data[i + 8 : i + 12]) else i + 6


def _is_high_surrogate(hex_digits: str) -> bool:
    return _code(hex_digits) in range(0xD800, 0xDC00)


def _is_low_surrogate(hex_digits: str) -> bool:
    return _code(hex_digits) in range(0xDC00, 0xE000)


def _code(hex_digits: str) -> int:
    try:
        return int(hex_digits, 16)
    except ValueError:
        return -1


def _unescape(sequence: str) -> str:
    """Decode one escape sequence; anything malformed becomes U+FFFD."""
    try:
        text = json.loads(f'"{sequence}"')
    except json.JSONDecodeError:
        return REPLACEMENT
    # A lone surrogate can't be encoded as UTF-8, so it can't reach the browser.
    return "".join(REPLACEMENT if "\ud800" <= c <= "\udfff" else c for c in text)
