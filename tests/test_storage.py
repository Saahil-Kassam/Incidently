"""Saving sessions to SQLite, and the history and progress built from them."""

import sqlite3
import threading
from datetime import datetime, timedelta

import pytest

from incidently.replies import Assessment, CommandResult, CriterionAssessment, StateUpdates
from incidently.storage import (
    MIGRATIONS,
    Attempt,
    Database,
    StorageError,
    Summary,
    progress,
    summarize,
)


def command_reply(stdout="ok\n"):
    return CommandResult(
        stdout=stdout,
        stderr="",
        exit_code=0,
        state_updates=StateUpdates(
            cwd="", user="", connect_to_host="", cli_mode="", services=[], new_facts=[]
        ),
    )


def assessment(*, met=True, resolved=True):
    return Assessment(
        criteria=[CriterionAssessment(id="find_cause", met=met, turn=1, feedback="f")],
        key_evidence_turn=0,
        resolved=resolved,
        resolved_turn=1 if resolved else 0,
        risk_events=[],
        summary="s",
        root_cause_explanation="r",
        strengths=[],
        improvements=[],
        ideal_path=[],
    )


def played(session, *, started_at, graded=None):
    """A copy of ``session`` with one simulated command, optionally ended and graded."""
    session = session.model_copy(deep=True)
    session.started_at = started_at
    session.record("dig example.com", command_reply(), model="m")
    if graded is not None:
        ended = datetime.fromisoformat(started_at) + timedelta(minutes=20)
        session.ended_at = ended.isoformat()
        session.assessment = graded
    return session


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "saves" / "test.db")
    yield database
    database.close()


# --- saving and loading -----------------------------------------------------------


def test_creates_the_folder_and_round_trips_a_session(db, session):
    session.record("dig example.com", command_reply("answer\n"), model="m")
    session.hints_given.append("Look at the forwarders.")
    db.save("abc", session)

    assert db.path.exists()
    assert db.load("abc") == session


def test_unknown_id_loads_as_none(db):
    assert db.load("nope") is None


def test_saving_again_replaces(db, session):
    db.save("abc", session)
    session.record("uptime", command_reply(), model="m")
    db.save("abc", session)

    assert db.load("abc").turn == 1
    assert len(db.attempts()) == 1


def test_sessions_persist_across_connections(tmp_path, session):
    first = Database(tmp_path / "test.db")
    first.save("abc", session)
    first.close()

    second = Database(tmp_path / "test.db")
    assert second.load("abc") == session
    second.close()


def test_concurrent_saves_from_threads(db, session):
    errors = []

    def save(i):
        try:
            db.save(f"s{i}", session)
        except Exception as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(db.attempts()) == 20


def test_unreadable_saved_session_is_a_storage_error(db, session):
    db.save("abc", session)
    with sqlite3.connect(db.path) as conn:
        conn.execute('UPDATE sessions SET data = \'{"not": "a session"}\'')
    with pytest.raises(StorageError, match="abc can't be read"):
        db.load("abc")


# --- schema versions -----------------------------------------------------------------


def test_new_file_is_at_the_latest_schema(db):
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_reopening_does_not_migrate_again(tmp_path, session):
    Database(tmp_path / "test.db").save("abc", session)
    reopened = Database(tmp_path / "test.db")  # would fail on "table already exists"
    assert reopened.load("abc") is not None


def test_file_from_a_newer_version_is_refused(tmp_path):
    path = tmp_path / "test.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    with pytest.raises(StorageError, match="newer version"):
        Database(path)


def test_unopenable_path_is_a_storage_error(tmp_path):
    (tmp_path / "test.db").mkdir()  # a folder where the file should be
    with pytest.raises(StorageError, match="can't open"):
        Database(tmp_path / "test.db")


# --- history ----------------------------------------------------------------------


def test_attempt_summary_before_and_after_grading(db, session):
    unfinished = played(session, started_at="2026-10-26T10:00:00+00:00")
    db.save("a", unfinished)
    (attempt,) = db.attempts()
    assert attempt == Attempt(
        session_id="a",
        scenario_id="lab-outage",
        title="Test Outage",
        started_at="2026-10-26T10:00:00+00:00",
        ended_at=None,
        commands=1,
        hints=0,
        graded=False,
    )
    assert attempt.status == "in_progress"
    assert attempt.duration_seconds is None

    db.save("a", played(session, started_at="2026-10-26T10:00:00+00:00", graded=assessment()))
    (attempt,) = db.attempts()
    assert attempt.graded and attempt.passed
    assert attempt.status == "passed"
    # Criterion points out of 100, plus the 10-point evidence bonus in the maximum.
    assert (attempt.total, attempt.maximum, attempt.percent) == (60, 110, 55)
    assert attempt.grade
    assert attempt.ended_at == "2026-10-26T10:20:00+00:00"
    assert attempt.duration_seconds == 20 * 60


def test_attempt_statuses():
    def attempt(**fields):
        base = dict(
            session_id="a",
            scenario_id="s",
            title="t",
            started_at="2026-10-26T10:00:00+00:00",
            ended_at=None,
            commands=0,
            hints=0,
            graded=False,
        )
        return Attempt(**(base | fields))

    assert attempt().status == "in_progress"
    assert attempt(ended_at="2026-10-26T10:05:00+00:00").status == "ungraded"
    assert attempt(graded=True, passed=False).status == "failed"
    assert attempt(graded=True, passed=True).status == "passed"


def test_attempts_are_listed_newest_first(db, session):
    db.save("old", played(session, started_at="2026-10-01T10:00:00+00:00"))
    db.save("new", played(session, started_at="2026-10-26T10:00:00+00:00"))
    db.save("mid", played(session, started_at="2026-10-15T10:00:00+00:00"))
    assert [a.session_id for a in db.attempts()] == ["new", "mid", "old"]


def test_attempts_started_the_same_second_keep_creation_order(db, session):
    same = "2026-10-26T10:00:00+00:00"
    db.save("first", played(session, started_at=same))
    db.save("second", played(session, started_at=same))
    db.save("first", played(session, started_at=same))  # updating doesn't move it
    assert [a.session_id for a in db.attempts()] == ["second", "first"]


# --- progress ------------------------------------------------------------------------


def make_attempts(db, session, specs):
    """Save one attempt per spec (newest last in ``specs``) and return them newest first."""
    for i, (session_id, graded) in enumerate(specs):
        db.save(
            session_id,
            played(session, started_at=f"2026-10-{10 + i}T10:00:00+00:00", graded=graded),
        )
    return db.attempts()


def test_progress_passed_beats_in_progress(db, session):
    attempts = make_attempts(
        db,
        session,
        [("fail", assessment(met=False)), ("pass", assessment()), ("open", None)],
    )
    record = progress(attempts)["lab-outage"]
    assert record.status == "passed"
    assert record.attempts == 3
    assert record.best.session_id == "pass"
    assert record.resume_id == "open"


def test_progress_in_progress_beats_attempted(db, session):
    attempts = make_attempts(db, session, [("fail", assessment(met=False)), ("open", None)])
    record = progress(attempts)["lab-outage"]
    assert record.status == "in_progress"
    assert record.best.session_id == "fail"
    assert record.resume_id == "open"


def test_progress_attempted_when_nothing_is_open_or_passed(db, session):
    attempts = make_attempts(db, session, [("fail", assessment(resolved=False))])
    record = progress(attempts)["lab-outage"]
    assert record.status == "attempted"
    assert record.resume_id is None


def test_progress_resumes_the_newest_unfinished_attempt(db, session):
    attempts = make_attempts(db, session, [("older", None), ("newer", None)])
    assert progress(attempts)["lab-outage"].resume_id == "newer"


def test_progress_leaves_out_unplayed_scenarios():
    assert progress([]) == {}


# --- totals ---------------------------------------------------------------------------


def test_summary_of_nothing():
    totals = summarize([])
    assert totals == Summary(attempts=0, graded=0, passed=0, seconds_played=0, average_percent=None)
    assert totals.pass_rate is None


def test_summary_counts_graded_attempts_and_time(db, session):
    attempts = make_attempts(
        db,
        session,
        [("fail", assessment(met=False)), ("pass", assessment()), ("open", None)],
    )
    totals = summarize(attempts)
    assert (totals.attempts, totals.graded, totals.passed) == (3, 2, 1)
    assert totals.pass_rate == 50
    assert totals.seconds_played == 2 * 20 * 60  # the unfinished one has no duration
    assert totals.average_percent == round((0 + 55) / 2)
