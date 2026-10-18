"""Runs a playthrough: sets up the incident and answers each command.

The engine is UI-agnostic. :meth:`Engine.start` returns a :class:`Session`;
:meth:`Engine.run` takes one line the player typed and returns a :class:`Turn`
describing what to show. Nothing here prints or knows about HTTP, so the web
app (and tests) drive it directly.

Built-ins like ``clear`` and ``exit`` are answered locally (see
:mod:`incidently.shell`); every other command is one model call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import prompts
from .catalog import ScenarioInstance
from .config import Settings
from .llm import LLMConfigError, LLMError, LLMRefusalError, LLMUnavailableError, StructuredLLM
from .replies import Bootstrap, CommandResult
from .session import Session
from .shell import Kind, classify, prompt

log = logging.getLogger(__name__)

BOOTSTRAP_MAX_TOKENS = 16_000
COMMAND_MAX_TOKENS = 8_000

# The model sees at most this many recent command exchanges. Older ones are
# dropped in chunks, not one at a time, so the conversation prefix stays stable
# between trims (good for prompt caching later). Established facts in the state
# snapshot keep continuity across the dropped turns.
HISTORY_TURNS = 40
TRIM_TURNS = 10


@dataclass(frozen=True)
class Turn:
    """What to show after one line of input."""

    prompt: str  # the prompt for the next command
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    clear_screen: bool = False
    end_requested: bool = False  # `exit` on the first host: the UI should confirm ending
    notice: str = ""  # a message from the simulator itself, not the host
    ok: bool = True  # False when the command could not be run (it was not recorded)


class Engine:
    def __init__(self, llm: StructuredLLM, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    def start(self, instance: ScenarioInstance) -> Session:
        """Generate the ticket and hidden facts, and return a session ready to play.

        Raises :class:`incidently.llm.LLMError` if setup fails; there is no
        session to fall back to.
        """
        log.info("starting %s seed=%d", instance.scenario.id, instance.seed)
        result = self._llm.generate(
            system=prompts.BOOTSTRAP_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": prompts.bootstrap_message(instance.scenario, instance.root_cause),
                }
            ],
            schema=Bootstrap,
            effort=self._settings.deep_effort,
            max_tokens=BOOTSTRAP_MAX_TOKENS,
        )
        return Session.create(instance, result.output)

    def prompt(self, session: Session) -> str:
        return prompt(session.active, remote=session.remote)

    def run(self, session: Session, line: str) -> Turn:
        """Handle one line of input and update ``session``."""
        c = classify(line, session.active)

        if c.kind is Kind.EMPTY:
            return self._turn(session)
        if c.kind is Kind.BLOCKED:
            return self._turn(session, notice=c.message, ok=False)
        if c.kind is Kind.CLEAR:
            session.record(c.command)
            return self._turn(session, clear_screen=True)
        if c.kind is Kind.HISTORY:
            session.record(c.command)
            listing = "\n".join(f"{r.turn:>5}  {r.command}" for r in session.history)
            return self._turn(session, stdout=listing + "\n")
        if c.kind is Kind.MODE:
            session.record(c.command)
            session.active.cli_mode = c.mode
            return self._turn(session)
        if c.kind is Kind.EXIT:
            if not session.remote:
                return self._turn(session, end_requested=True)
            session.record(c.command)
            left = session.active.name
            session.disconnect()
            return self._turn(session, stdout=f"Connection to {left} closed.\n")
        return self._simulate(session, c.command)

    def _simulate(self, session: Session, command: str) -> Turn:
        message = {"role": "user", "content": prompts.command_message(session, command)}
        try:
            result = self._llm.generate(
                system=prompts.simulation_system(session),
                messages=[*session.messages, message],
                schema=CommandResult,
                effort=self._settings.turn_effort,
                max_tokens=COMMAND_MAX_TOKENS,
            )
        except LLMError as exc:
            log.warning("command not simulated (%s): %s", type(exc).__name__, exc)
            return self._turn(session, notice=_failure_notice(exc), ok=False)

        reply = result.output
        session.record(command, reply, model=result.model)
        session.apply(reply)
        session.messages += [message, {"role": "assistant", "content": reply.model_dump_json()}]
        _trim(session)
        return self._turn(
            session, stdout=reply.stdout, stderr=reply.stderr, exit_code=reply.exit_code
        )

    def _turn(self, session: Session, **fields: object) -> Turn:
        return Turn(prompt=self.prompt(session), **fields)  # type: ignore[arg-type]


def _trim(session: Session) -> None:
    turns = len(session.messages) // 2
    if turns > HISTORY_TURNS:
        drop = max(TRIM_TURNS, turns - HISTORY_TURNS)
        session.messages = session.messages[drop * 2 :]


def _failure_notice(exc: LLMError) -> str:
    if isinstance(exc, LLMConfigError):
        return f"The simulator can't reach the model: {exc}"
    if isinstance(exc, LLMUnavailableError):
        return "The simulator is busy right now. Try the command again in a moment."
    if isinstance(exc, LLMRefusalError):
        return "The simulator declined that command. Try a different approach."
    return "The simulator couldn't produce output for that command. Try it again."
