"""Scoring and the debrief.

The grading call decides *which* criteria were met, since only a model can
read simulated output and judge it. Everything numeric happens here, so a
score is reproducible from the session and the model can't be talked into a
higher one.

The model's verdict is also checked before it counts: criteria it names
that the scenario doesn't have are ignored, a criterion only counts as met if
the model cites the simulated command that met it, and nothing can be met or
resolved in a session where no command was simulated.

Score = criterion points + evidence bonus - hint and risk penalties, floored
at 0. A pass needs the incident resolved and every required criterion met.
"""

from __future__ import annotations

from dataclasses import dataclass

from .replies import Assessment
from .session import Session

EVIDENCE_BONUS = 10
HINT_PENALTY = 5
RISK_PENALTY = {"high": 5, "destructive": 15}
GRADE_FLOORS = ((90, "A"), (80, "B"), (70, "C"), (60, "D"))


@dataclass(frozen=True)
class Checkpoint:
    id: str
    description: str
    points: int
    required: bool
    met: bool
    turn: int | None  # the command that first met it
    feedback: str


@dataclass(frozen=True)
class Risk:
    turn: int
    command: str
    level: str
    reason: str
    penalty: int


@dataclass(frozen=True)
class Score:
    earned: int  # criterion points
    bonus: int
    hint_penalty: int
    risk_penalty: int
    total: int
    maximum: int
    percent: int
    grade: str
    passed: bool
    resolved: bool
    missing_required: tuple[str, ...]


@dataclass(frozen=True)
class Debrief:
    scenario_id: str
    title: str
    root_cause: str
    score: Score
    checkpoints: tuple[Checkpoint, ...]
    risks: tuple[Risk, ...]
    key_evidence_turn: int | None
    resolved_turn: int | None
    summary: str
    root_cause_explanation: str
    strengths: tuple[str, ...]
    improvements: tuple[str, ...]
    ideal_path: tuple[str, ...]
    commands_run: int
    hints_used: int


def debrief(session: Session) -> Debrief:
    """Build the debrief for a graded session."""
    assessment = session.assessment
    if assessment is None:
        raise ValueError("the session has not been graded yet")

    simulated = {r.turn: r.command for r in session.history if not r.local}

    def real(turn: int) -> int | None:
        return turn if turn in simulated else None

    verdicts = {c.id: c for c in assessment.criteria}
    checkpoints = []
    for criterion in session.scenario.grading:
        verdict = verdicts.get(criterion.id)
        turn = real(verdict.turn) if verdict is not None and verdict.met else None
        checkpoints.append(
            Checkpoint(
                id=criterion.id,
                description=criterion.description,
                points=criterion.points,
                required=criterion.required,
                met=turn is not None,
                turn=turn,
                feedback=verdict.feedback.strip() if verdict else "",
            )
        )

    risks = tuple(
        sorted(
            (
                Risk(e.turn, simulated[e.turn], e.level, e.reason.strip(), RISK_PENALTY[e.level])
                for e in _unique_risks(assessment)
                if e.turn in simulated
            ),
            key=lambda r: r.turn,
        )
    )
    key_evidence_turn = real(assessment.key_evidence_turn)
    resolved = assessment.resolved and bool(simulated)
    resolved_turn = real(assessment.resolved_turn) if resolved else None
    hints_used = len(session.hints_given)

    return Debrief(
        scenario_id=session.scenario.id,
        title=session.scenario.title,
        root_cause=session.root_cause,
        score=_score(checkpoints, risks, key_evidence_turn, resolved, hints_used),
        checkpoints=tuple(checkpoints),
        risks=risks,
        key_evidence_turn=key_evidence_turn,
        resolved_turn=resolved_turn,
        summary=assessment.summary.strip(),
        root_cause_explanation=assessment.root_cause_explanation.strip(),
        strengths=_clean(assessment.strengths),
        improvements=_clean(assessment.improvements),
        ideal_path=_clean(assessment.ideal_path),
        commands_run=session.commands_run,
        hints_used=hints_used,
    )


def _score(
    checkpoints: list[Checkpoint],
    risks: tuple[Risk, ...],
    key_evidence_turn: int | None,
    resolved: bool,
    hints_used: int,
) -> Score:
    earned = sum(c.points for c in checkpoints if c.met)
    bonus = EVIDENCE_BONUS if key_evidence_turn is not None else 0
    hint_penalty = hints_used * HINT_PENALTY
    risk_penalty = sum(r.penalty for r in risks)
    maximum = sum(c.points for c in checkpoints) + EVIDENCE_BONUS
    total = max(0, earned + bonus - hint_penalty - risk_penalty)
    percent = round(100 * total / maximum)
    missing = tuple(c.id for c in checkpoints if c.required and not c.met)
    passed = resolved and not missing
    return Score(
        earned=earned,
        bonus=bonus,
        hint_penalty=hint_penalty,
        risk_penalty=risk_penalty,
        total=total,
        maximum=maximum,
        percent=percent,
        grade=_grade(percent, passed),
        passed=passed,
        resolved=resolved,
        missing_required=missing,
    )


def _grade(percent: int, passed: bool) -> str:
    """Letter grade. An unpassed incident is at best a D, however many points it earned."""
    if not passed:
        return "D" if percent >= 60 else "F"
    return next((grade for floor, grade in GRADE_FLOORS if percent >= floor), "D")


def _unique_risks(assessment: Assessment):
    """One risk per command: the model sometimes lists the same command twice."""
    seen: set[int] = set()
    for event in sorted(assessment.risk_events, key=lambda e: e.level == "high"):
        if event.turn not in seen:  # "destructive" sorts first, so it wins
            seen.add(event.turn)
            yield event


def _clean(items: list[str]) -> tuple[str, ...]:
    return tuple(item.strip() for item in items if item.strip())
