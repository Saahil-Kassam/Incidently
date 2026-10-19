"""Scoring rules. The lab scenario has two criteria: find_cause (60, required)
and verify_fix (40, optional), so the maximum is 100 + the evidence bonus."""

import pytest

from incidently.grading import (
    EVIDENCE_BONUS,
    HINT_PENALTY,
    RISK_PENALTY,
    _grade,
    debrief,
)
from incidently.replies import (
    Assessment,
    CommandResult,
    CriterionAssessment,
    RiskEvent,
    StateUpdates,
)

NO_UPDATES = StateUpdates(
    cwd="", user="", connect_to_host="", cli_mode="", services=[], new_facts=[]
)


def simulate(session, *commands):
    for command in commands:
        session.record(
            command,
            CommandResult(stdout="ok\n", stderr="", exit_code=0, state_updates=NO_UPDATES),
            model="m",
        )


def assessment(criteria=(), risks=(), key_evidence_turn=0, resolved=False, resolved_turn=0):
    return Assessment(
        criteria=[
            CriterionAssessment(id=i, met=True, turn=t, feedback=f"{i} ok") for i, t in criteria
        ],
        key_evidence_turn=key_evidence_turn,
        resolved=resolved,
        resolved_turn=resolved_turn,
        risk_events=[RiskEvent(turn=t, level=lvl, reason="r") for t, lvl in risks],
        summary=" Summary. ",
        root_cause_explanation="Because.",
        strengths=["good", "  "],
        improvements=["better"],
        ideal_path=["dig example.com"],
    )


@pytest.fixture
def played(session):
    simulate(session, "dig example.com", "systemctl restart unbound", "dig example.com")
    return session


def test_perfect_run(played):
    played.assessment = assessment(
        criteria=[("find_cause", 1), ("verify_fix", 3)],
        key_evidence_turn=1,
        resolved=True,
        resolved_turn=2,
    )
    d = debrief(played)
    s = d.score

    assert (s.earned, s.bonus, s.total, s.maximum) == (100, EVIDENCE_BONUS, 110, 110)
    assert (s.percent, s.grade, s.passed) == (100, "A", True)
    assert [c.met for c in d.checkpoints] == [True, True]
    assert [c.turn for c in d.checkpoints] == [1, 3]
    assert d.key_evidence_turn == 1
    assert d.resolved_turn == 2
    assert d.root_cause == played.root_cause
    assert d.commands_run == 3


def test_missing_required_criterion_fails_even_if_resolved(played):
    played.assessment = assessment(criteria=[("verify_fix", 3)], resolved=True, resolved_turn=2)
    s = debrief(played).score
    assert not s.passed
    assert s.missing_required == ("find_cause",)
    assert s.grade == "F"  # 40/110


def test_unresolved_incident_caps_the_grade_at_d(played):
    played.assessment = assessment(
        criteria=[("find_cause", 1), ("verify_fix", 3)], key_evidence_turn=1, resolved=False
    )
    s = debrief(played).score
    assert s.percent == 100
    assert (s.passed, s.grade) == (False, "D")


def test_hints_and_risks_cost_points(played):
    played.hints_given = ["h1", "h2"]
    played.assessment = assessment(
        criteria=[("find_cause", 1), ("verify_fix", 3)],
        risks=[(2, "high"), (3, "destructive")],
        resolved=True,
        resolved_turn=2,
    )
    d = debrief(played)
    assert d.score.hint_penalty == 2 * HINT_PENALTY
    assert d.score.risk_penalty == RISK_PENALTY["high"] + RISK_PENALTY["destructive"]
    assert d.score.total == 100 - 10 - 20
    assert [(r.turn, r.command, r.level) for r in d.risks] == [
        (2, "systemctl restart unbound", "high"),
        (3, "dig example.com", "destructive"),
    ]
    assert d.hints_used == 2


def test_score_never_goes_negative(played):
    played.hints_given = ["h"] * 3
    played.assessment = assessment(risks=[(1, "destructive"), (2, "destructive")])
    assert debrief(played).score.total == 0


def test_a_command_listed_twice_as_risky_is_penalised_once_at_the_worst_level(played):
    played.assessment = assessment(risks=[(2, "high"), (2, "destructive")])
    (risk,) = debrief(played).risks
    assert risk.level == "destructive"


# --- the model's verdict is checked, not trusted ------------------------------


def test_unknown_criteria_are_ignored(played):
    played.assessment = assessment(criteria=[("made_up", 1)])
    d = debrief(played)
    assert d.score.earned == 0
    assert [c.id for c in d.checkpoints] == ["find_cause", "verify_fix"]


@pytest.mark.parametrize("turn", [0, 99, 4])
def test_a_criterion_needs_a_real_simulated_turn(played, turn):
    played.record("clear")  # turn 4 is a local built-in
    played.assessment = assessment(criteria=[("find_cause", turn)])
    assert debrief(played).checkpoints[0].met is False


def test_risks_on_unknown_or_local_turns_are_dropped(played):
    played.record("clear")
    played.assessment = assessment(risks=[(4, "destructive"), (42, "high")])
    assert debrief(played).risks == ()


def test_nothing_counts_when_no_command_was_simulated(session):
    session.assessment = assessment(
        criteria=[("find_cause", 1)], key_evidence_turn=1, resolved=True, resolved_turn=1
    )
    d = debrief(session)
    assert d.score.total == 0
    assert not d.score.resolved
    assert d.key_evidence_turn is None


def test_debrief_text_is_cleaned(played):
    played.assessment = assessment()
    d = debrief(played)
    assert d.summary == "Summary."
    assert d.strengths == ("good",)


def test_ungraded_session_has_no_debrief(session):
    with pytest.raises(ValueError, match="not been graded"):
        debrief(session)


@pytest.mark.parametrize(
    ("percent", "passed", "grade"),
    [
        (95, True, "A"),
        (90, True, "A"),
        (85, True, "B"),
        (70, True, "C"),
        (40, True, "D"),
        (95, False, "D"),
        (59, False, "F"),
    ],
)
def test_letter_grades(percent, passed, grade):
    assert _grade(percent, passed) == grade
