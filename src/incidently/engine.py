"""Runs a playthrough: sets up the incident and answers each command.

The engine is UI-agnostic. :meth:`Engine.start` returns a :class:`Session`;
:meth:`Engine.run` takes one line the player typed and returns a :class:`Turn`
describing what to show. Nothing here prints or knows about HTTP, so the web
app (and tests) drive it directly.

Built-ins like ``clear`` and ``exit`` are answered locally (see
:mod:`incidently.shell`); every other command is one model call.

:meth:`Engine.hint` writes a hint for where the player is, and
:meth:`Engine.finish` ends the session, grades it, and returns the
:class:`incidently.grading.Debrief`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import prompts
from .catalog import ScenarioInstance
from .config import Settings
from .grading import Debrief, debrief
from .llm import LLMConfigError, LLMError, LLMRefusalError, LLMUnavailableError, StructuredLLM
from .replies import Assessment, Bootstrap, CommandResult, HintReply
from .session import Session, now_utc
from .shell import Kind, classify, prompt

log = logging.getLogger(__name__)

BOOTSTRAP_MAX_TOKENS = 16_000
COMMAND_MAX_TOKENS = 8_000
HINT_MAX_TOKENS = 8_000
# A hint repeating this many consecutive words of the hidden answer is a leak.
LEAK_WORDS = 8
ASSESSMENT_MAX_TOKENS = 32_000

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


@dataclass(frozen=True)
class Hint:
    number: int
    total: int
    text: str
    written: bool  # True if the model wrote it for this moment, False for the scenario's own


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
        started_at = now_utc()
        result = self._llm.generate(
            system=prompts.BOOTSTRAP_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": prompts.bootstrap_message(
                        instance.scenario, instance.root_cause, started_at
                    ),
                }
            ],
            schema=Bootstrap,
            effort=self._settings.deep_effort,
            max_tokens=BOOTSTRAP_MAX_TOKENS,
        )
        return Session.create(instance, result.output, started_at=started_at)

    def prompt(self, session: Session) -> str:
        return prompt(session.active, remote=session.remote)

    def run(self, session: Session, line: str) -> Turn:
        """Handle one line of input and update ``session``."""
        if session.ended:
            return self._turn(session, notice="This incident has ended.", ok=False)
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
        # Keep only the command in the conversation: the snapshot sent with it
        # is stale after this turn, and the next turn sends a fresh one.
        past = {"role": "user", "content": prompts.command_tag(session, command)}
        session.record(command, reply, model=result.model)
        session.apply(reply)
        session.messages += [past, {"role": "assistant", "content": reply.model_dump_json()}]
        _trim(session)
        return self._turn(
            session, stdout=reply.stdout, stderr=reply.stderr, exit_code=reply.exit_code
        )

    def hint(self, session: Session) -> Hint | None:
        """The next hint, or None once the scenario's hints are used up or the session ended.

        The model writes it from the player's progress so far. If that call
        fails, or the hint quotes the hidden answer, the scenario's own hint
        for this slot is used instead, so asking for a hint always works.
        Each hint costs points at grading time.
        """
        total = len(session.scenario.hints)
        number = len(session.hints_given) + 1
        if session.ended or number > total:
            return None

        text = ""
        try:
            result = self._llm.generate(
                system=prompts.HINT_SYSTEM,
                messages=[
                    {"role": "user", "content": prompts.hint_message(session, number, total)}
                ],
                schema=HintReply,
                effort=self._settings.turn_effort,
                max_tokens=HINT_MAX_TOKENS,
            )
            text = result.output.hint.strip()
        except LLMError as exc:
            log.warning("hint not written (%s): %s", type(exc).__name__, exc)
        if text and _reveals_answer(text, session):
            log.warning("written hint quoted the hidden answer; using the scenario's hint")
            text = ""

        written = bool(text)
        text = text or session.scenario.hints[number - 1]
        session.hints_given.append(text)
        return Hint(number, total, text, written)

    def finish(self, session: Session) -> Debrief:
        """End the session, grade it, and return the debrief.

        Grading happens once; calling this again returns the same debrief
        without another model call. If the grading call fails the error is
        raised and the session stays ended but ungraded, so the caller can
        retry.
        """
        session.end()
        if session.assessment is None:
            result = self._llm.generate(
                system=prompts.ASSESSMENT_SYSTEM,
                messages=[{"role": "user", "content": prompts.assessment_message(session)}],
                schema=Assessment,
                effort=self._settings.deep_effort,
                max_tokens=ASSESSMENT_MAX_TOKENS,
            )
            session.assessment = result.output
        return debrief(session)

    def _turn(self, session: Session, **fields: object) -> Turn:
        return Turn(prompt=self.prompt(session), **fields)  # type: ignore[arg-type]


def _trim(session: Session) -> None:
    turns = len(session.messages) // 2
    if turns > HISTORY_TURNS:
        drop = max(TRIM_TURNS, turns - HISTORY_TURNS)
        session.messages = session.messages[drop * 2 :]


def _reveals_answer(hint: str, session: Session) -> bool:
    """Whether a hint quotes a long run of the root cause or key evidence word for word.

    A backstop for the hint prompt's own rule: paraphrase is allowed, copying
    the hidden text is not.
    """
    words = _words(hint)
    for secret in (session.root_cause, session.scenario.hidden_flag):
        secret_words = _words(secret)
        if not secret_words:
            continue
        span = min(LEAK_WORDS, len(secret_words))
        for i in range(len(secret_words) - span + 1):
            if _contains(words, secret_words[i : i + span]):
                return True
    return False


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _contains(words: list[str], run: list[str]) -> bool:
    n = len(run)
    return any(words[i : i + n] == run for i in range(len(words) - n + 1))


def _failure_notice(exc: LLMError) -> str:
    if isinstance(exc, LLMConfigError):
        return f"The simulator can't reach the model: {exc}"
    if isinstance(exc, LLMUnavailableError):
        return "The simulator is busy right now. Try the command again in a moment."
    if isinstance(exc, LLMRefusalError):
        return "The simulator declined that command. Try a different approach."
    return "The simulator couldn't produce output for that command. Try it again."
