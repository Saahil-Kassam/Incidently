"""Canned replies for playing without the API (``INCIDENTLY_FAKE_LLM=1``).

They make the whole app usable offline (for UI work, demos, and tests that
drive a full session), not a real simulation. Every command gets the same
kind of placeholder output, and ``cd``, ``ssh`` and IOS mode changes update
the state so the prompt behaves believably. Hints fall back to the
scenario's own, and grading marks nothing as met.
"""

from __future__ import annotations

import re
import shlex

from pydantic import BaseModel

from .llm import LLMRequest
from .replies import Assessment, Bootstrap, CommandResult, HintReply, StateUpdates, Ticket

_COMMAND = re.compile(r"<command [^>]*>(.*)</command>", re.DOTALL)
_HOST = re.compile(r'<command host="([^"]*)"')

_IOS_MODES = {
    "configure terminal": "config",
    "conf t": "config",
    "interface": "config-if",
    "int": "config-if",
    "router": "config-router",
    "line": "config-line",
}


def offline_reply(request: LLMRequest) -> BaseModel:
    if request.schema is Bootstrap:
        return _bootstrap()
    if request.schema is CommandResult:
        return _command(str(request.messages[-1]["content"]))
    if request.schema is HintReply:
        return HintReply(hint="")  # empty: the engine uses the scenario's own hint
    if request.schema is Assessment:
        return _assessment()
    raise TypeError(f"offline mode has no reply for {request.schema.__name__}")


def _bootstrap() -> Bootstrap:
    return Bootstrap(
        ticket=Ticket(
            id="INC0000000",
            priority="P3",
            reported_by="Offline mode",
            summary="Offline practice session",
            description=(
                "Incidently is running with INCIDENTLY_FAKE_LLM=1, so hosts reply with "
                "placeholder output. Set it to 0 and add an API key for a real incident."
            ),
        ),
        facts=["This session runs in offline mode with placeholder output."],
    )


def _command(content: str) -> CommandResult:
    command_match = _COMMAND.search(content)
    host_match = _HOST.search(content)
    command = command_match.group(1).strip() if command_match else ""
    host = host_match.group(1) if host_match else "host"
    updates = StateUpdates(
        cwd="", user="", connect_to_host="", cli_mode="", services=[], new_facts=[]
    )

    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    program = words[0].lower() if words else ""
    lowered = " ".join(w.lower() for w in words)

    if program == "cd" and len(words) > 1:
        updates.cwd = words[1]
        return _result("", updates)
    if program in ("ssh", "telnet") and len(words) > 1:
        updates.connect_to_host = words[-1].rsplit("@", 1)[-1]
        return _result(f"[offline] connected to {updates.connect_to_host}\n", updates)
    if program == "enter-pssession" and len(words) > 1:
        updates.connect_to_host = words[-1]
        return _result("", updates)
    for prefix, mode in _IOS_MODES.items():
        if lowered == prefix or lowered.startswith(prefix + " "):
            updates.cli_mode = mode
            return _result("", updates)
    return _result(f"[offline] {host}: placeholder output for `{command}`\n", updates)


def _result(stdout: str, updates: StateUpdates) -> CommandResult:
    return CommandResult(stdout=stdout, stderr="", exit_code=0, state_updates=updates)


def _assessment() -> Assessment:
    return Assessment(
        criteria=[],
        key_evidence_turn=0,
        resolved=False,
        resolved_turn=0,
        risk_events=[],
        summary="Offline mode: this session was not graded.",
        root_cause_explanation="Grading needs the model; set INCIDENTLY_FAKE_LLM=0 to be graded.",
        strengths=[],
        improvements=[],
        ideal_path=[],
    )
