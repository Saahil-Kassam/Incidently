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

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel

from ..authoring import Generated
from ..engine import Hint, Turn
from ..grading import Debrief
from ..llm import Usage
from ..scenario import Scenario
from ..session import Session
from ..storage import (
    Attempt,
    AttemptStatus,
    GenerationTotals,
    Progress,
    ProgressStatus,
    progress,
    summarize,
)


class UsageView(BaseModel):
    """Model tokens used and their estimated cost. ``cost_usd`` is null when unknown."""

    input_tokens: int  # uncached input
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    cached_percent: int | None  # share of all input tokens read from cache
    cost_usd: float | None

    @classmethod
    def of(cls, usage: Usage, cost_usd: float | None) -> UsageView:
        all_input = (
            usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens
        )
        return cls(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens,
            cache_write_tokens=usage.cache_creation_input_tokens,
            total_tokens=all_input + usage.output_tokens,
            cached_percent=round(100 * usage.cache_read_input_tokens / all_input)
            if all_input
            else None,
            cost_usd=cost_usd,
        )

    @classmethod
    def of_session(cls, session: Session) -> UsageView:
        return cls.of(session.total_usage, session.estimated_cost())


class ScenarioCard(BaseModel):
    id: str
    title: str
    category: str
    topic: str
    difficulty: str
    summary: str
    skills: list[str]
    custom: bool  # generated from a theme, so the player can delete it

    @classmethod
    def of(cls, scenario: Scenario, *, custom: bool = False) -> ScenarioCard:
        return cls(
            id=scenario.id,
            title=scenario.title,
            category=scenario.category,
            topic=scenario.topic,
            difficulty=scenario.difficulty,
            summary=scenario.summary,
            skills=list(scenario.skills),
            custom=custom,
        )


class GeneratedView(BaseModel):
    """A scenario just written from a theme, and what writing it cost."""

    scenario: ScenarioCard
    usage: UsageView

    @classmethod
    def of(cls, generated: Generated) -> GeneratedView:
        return cls(
            scenario=ScenarioCard.of(generated.scenario, custom=True),
            usage=UsageView.of(generated.usage, generated.estimated_cost()),
        )


class ProgressView(BaseModel):
    """The player's record on one scenario, for the hub's badges."""

    scenario_id: str
    status: ProgressStatus
    attempts: int
    best_percent: int | None
    best_grade: str | None
    resume_id: str | None  # an attempt not yet graded, to pick up again

    @classmethod
    def of(cls, scenario_id: str, progress: Progress | None) -> ProgressView:
        if progress is None:
            return cls(
                scenario_id=scenario_id,
                status="not_started",
                attempts=0,
                best_percent=None,
                best_grade=None,
                resume_id=None,
            )
        best = progress.best
        return cls(
            scenario_id=scenario_id,
            status=progress.status,
            attempts=progress.attempts,
            best_percent=best.percent if best else None,
            best_grade=best.grade if best else None,
            resume_id=progress.resume_id,
        )


class AttemptView(BaseModel):
    """One past or unfinished attempt in the history. Score fields are null until graded."""

    session_id: str
    scenario_id: str
    title: str
    status: AttemptStatus
    started_at: str
    ended_at: str | None
    duration_seconds: int | None
    commands: int
    hints: int
    total: int | None
    maximum: int | None
    percent: int | None
    grade: str | None
    usage: UsageView

    @classmethod
    def of(cls, attempt: Attempt) -> AttemptView:
        return cls(
            session_id=attempt.session_id,
            scenario_id=attempt.scenario_id,
            title=attempt.title,
            status=attempt.status,
            started_at=attempt.started_at,
            ended_at=attempt.ended_at,
            duration_seconds=attempt.duration_seconds,
            commands=attempt.commands,
            hints=attempt.hints,
            total=attempt.total,
            maximum=attempt.maximum,
            percent=attempt.percent,
            grade=attempt.grade,
            usage=UsageView.of(attempt.usage, attempt.cost_usd),
        )


class ScenarioDetailView(BaseModel):
    """A scenario's own page: what it is, what it practises, and the player's attempts."""

    scenario: ScenarioCard
    description: str
    progress: ProgressView
    attempts: list[AttemptView]  # newest first

    @classmethod
    def of(
        cls, scenario: Scenario, attempts: list[Attempt], *, custom: bool = False
    ) -> ScenarioDetailView:
        mine = [a for a in attempts if a.scenario_id == scenario.id]
        return cls(
            scenario=ScenarioCard.of(scenario, custom=custom),
            description=scenario.description,
            progress=ProgressView.of(scenario.id, progress(mine).get(scenario.id)),
            attempts=[AttemptView.of(a) for a in mine],
        )


class SkillView(BaseModel):
    skill: str
    incidents: int  # scenarios in the catalog that practise it
    passed: int  # of those, how many the player has passed


class StatsView(BaseModel):
    """Totals for the progress dashboard."""

    incidents: int
    incidents_started: int
    incidents_passed: int
    attempts: int
    graded: int
    passed_attempts: int
    pass_rate: int | None  # percent of graded attempts that passed
    average_percent: int | None
    seconds_played: int
    generations: int  # custom incidents written, including failed attempts
    usage: UsageView  # attempts plus generations; cost_usd sums those whose cost is known
    cost_complete: bool  # False if some attempt's or generation's cost is unknown
    skills: list[SkillView]  # most widely practised first

    @classmethod
    def of(
        cls,
        scenarios: Iterable[Scenario],
        attempts: list[Attempt],
        generations: GenerationTotals | None = None,
    ) -> StatsView:
        scenarios = list(scenarios)
        records = progress(attempts)
        passed = {sid for sid, p in records.items() if p.status == "passed"}
        totals = summarize(attempts)
        generations = generations or GenerationTotals()

        skills: dict[str, SkillView] = {}
        for scenario in scenarios:
            for name in scenario.skills:
                view = skills.setdefault(name, SkillView(skill=name, incidents=0, passed=0))
                view.incidents += 1
                view.passed += scenario.id in passed

        return cls(
            incidents=len(scenarios),
            incidents_started=sum(1 for s in scenarios if s.id in records),
            incidents_passed=sum(1 for s in scenarios if s.id in passed),
            attempts=totals.attempts,
            graded=totals.graded,
            passed_attempts=totals.passed,
            pass_rate=totals.pass_rate,
            average_percent=totals.average_percent,
            seconds_played=totals.seconds_played,
            generations=generations.generations,
            usage=UsageView.of(
                totals.usage + generations.usage, totals.cost_usd + generations.cost_usd
            ),
            cost_complete=totals.cost_complete and generations.cost_complete,
            skills=sorted(skills.values(), key=lambda s: (-s.incidents, s.skill.lower())),
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
    usage: UsageView

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
            usage=UsageView.of_session(session),
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
    usage: UsageView  # the session's running total after this turn

    @classmethod
    def of(cls, turn: Turn, session: Session) -> TurnView:
        return cls(
            prompt=turn.prompt,
            stdout=turn.stdout,
            stderr=turn.stderr,
            exit_code=turn.exit_code,
            clear_screen=turn.clear_screen,
            end_requested=turn.end_requested,
            notice=turn.notice,
            ok=turn.ok,
            usage=UsageView.of_session(session),
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
    usage: UsageView  # the session's running total after this hint

    @classmethod
    def of(cls, hint: Hint, session: Session) -> HintView:
        return cls(
            number=hint.number,
            total=hint.total,
            text=hint.text,
            usage=UsageView.of_session(session),
        )


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
    usage: UsageView

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
            usage=UsageView.of_session(session),
        )
