"""What the browser is allowed to see.

Every API response is one of these models, built field by field from the
session. Nothing is serialised from a :class:`Session` or :class:`Scenario`
directly, so the hidden root cause, key evidence, grading criteria,
established facts, simulation notes, and the model conversation can't reach
the browser by accident. The root cause is revealed only in the debrief,
after the session is graded.

A streamed command sends one event per line (newline-delimited JSON): any
number of ``output`` and ``restart`` events, then one ``turn`` or ``error``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..engine import Hint, Turn
from ..grading import Debrief
from ..scenario import Scenario
from ..session import Session


class ScenarioCard(BaseModel):
    id: str
    title: str
    category: str
    topic: str
    difficulty: str
    summary: str

    @classmethod
    def of(cls, scenario: Scenario) -> ScenarioCard:
        return cls(
            id=scenario.id,
            title=scenario.title,
            category=scenario.category,
            topic=scenario.topic,
            difficulty=scenario.difficulty,
            summary=scenario.summary,
        )


class HostView(BaseModel):
    name: str
    os: str
    shell: str
    ip: str
    role: str


class TicketView(BaseModel):
    id: str
    priority: str
    reported_by: str
    summary: str
    description: str


class TranscriptEntry(BaseModel):
    prompt: str
    command: str
    stdout: str
    stderr: str


class SessionView(BaseModel):
    id: str
    scenario: ScenarioCard
    objective: str
    ticket: TicketView
    hosts: list[HostView]
    start_host: str
    prompt: str
    transcript: list[TranscriptEntry]
    hints: list[str]
    hints_total: int
    ended: bool
    graded: bool

    @classmethod
    def of(cls, session_id: str, session: Session, prompt: str) -> SessionView:
        scenario = session.scenario
        ticket = session.ticket
        return cls(
            id=session_id,
            scenario=ScenarioCard.of(scenario),
            objective=scenario.objective,
            ticket=TicketView(
                id=ticket.id,
                priority=ticket.priority,
                reported_by=ticket.reported_by,
                summary=ticket.summary,
                description=ticket.description,
            ),
            hosts=[
                HostView(name=h.name, os=h.os, shell=h.shell, ip=h.ip, role=h.role)
                for h in scenario.hosts
            ],
            start_host=scenario.start_host,
            prompt=prompt,
            transcript=[
                TranscriptEntry(
                    prompt=r.prompt, command=r.command, stdout=r.stdout, stderr=r.stderr
                )
                for r in session.history
            ],
            hints=list(session.hints_given),
            hints_total=len(scenario.hints),
            ended=session.ended,
            graded=session.assessment is not None,
        )


class TurnView(BaseModel):
    prompt: str
    stdout: str
    stderr: str
    exit_code: int | None
    clear_screen: bool
    end_requested: bool
    notice: str
    ok: bool

    @classmethod
    def of(cls, turn: Turn) -> TurnView:
        return cls(
            prompt=turn.prompt,
            stdout=turn.stdout,
            stderr=turn.stderr,
            exit_code=turn.exit_code,
            clear_screen=turn.clear_screen,
            end_requested=turn.end_requested,
            notice=turn.notice,
            ok=turn.ok,
        )


class OutputEvent(BaseModel):
    """More of the command's stdout, while the model is still writing it."""

    type: Literal["output"] = "output"
    text: str


class RestartEvent(BaseModel):
    """The output streamed so far is void; it is being written again."""

    type: Literal["restart"] = "restart"


class TurnEvent(BaseModel):
    """The command is done. ``turn.stdout`` is the complete output."""

    type: Literal["turn"] = "turn"
    turn: TurnView


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    detail: str


class HintView(BaseModel):
    number: int
    total: int
    text: str

    @classmethod
    def of(cls, hint: Hint) -> HintView:
        return cls(number=hint.number, total=hint.total, text=hint.text)


class CheckpointView(BaseModel):
    description: str
    points: int
    required: bool
    met: bool
    turn: int | None
    feedback: str


class RiskView(BaseModel):
    turn: int
    command: str
    level: str
    reason: str
    penalty: int


class ScoreView(BaseModel):
    earned: int
    bonus: int
    hint_penalty: int
    risk_penalty: int
    total: int
    maximum: int
    percent: int
    grade: str
    passed: bool
    resolved: bool


class DebriefView(BaseModel):
    session_id: str
    scenario: ScenarioCard
    root_cause: str
    score: ScoreView
    checkpoints: list[CheckpointView]
    risks: list[RiskView]
    key_evidence_turn: int | None
    resolved_turn: int | None
    summary: str
    root_cause_explanation: str
    strengths: list[str]
    improvements: list[str]
    ideal_path: list[str]
    commands_run: int
    hints_used: int

    @classmethod
    def of(cls, session_id: str, session: Session, debrief: Debrief) -> DebriefView:
        s = debrief.score
        return cls(
            session_id=session_id,
            scenario=ScenarioCard.of(session.scenario),
            root_cause=debrief.root_cause,
            score=ScoreView(
                earned=s.earned,
                bonus=s.bonus,
                hint_penalty=s.hint_penalty,
                risk_penalty=s.risk_penalty,
                total=s.total,
                maximum=s.maximum,
                percent=s.percent,
                grade=s.grade,
                passed=s.passed,
                resolved=s.resolved,
            ),
            checkpoints=[
                CheckpointView(
                    description=c.description,
                    points=c.points,
                    required=c.required,
                    met=c.met,
                    turn=c.turn,
                    feedback=c.feedback,
                )
                for c in debrief.checkpoints
            ],
            risks=[
                RiskView(
                    turn=r.turn,
                    command=r.command,
                    level=r.level,
                    reason=r.reason,
                    penalty=r.penalty,
                )
                for r in debrief.risks
            ],
            key_evidence_turn=debrief.key_evidence_turn,
            resolved_turn=debrief.resolved_turn,
            summary=debrief.summary,
            root_cause_explanation=debrief.root_cause_explanation,
            strengths=list(debrief.strengths),
            improvements=list(debrief.improvements),
            ideal_path=list(debrief.ideal_path),
            commands_run=debrief.commands_run,
            hints_used=debrief.hints_used,
        )
