"""Saved sessions, in a local SQLite database.

Each session is stored whole, as the JSON of :class:`Session`. It carries
its own copy of the scenario, so an old attempt still opens after the
scenario file changes. Summary columns beside it (title, times, counts, and
the score once graded) let the hub list history and progress without reading
every session.

The schema version lives in ``PRAGMA user_version``. Opening a file runs
each migration it hasn't had yet, in its own transaction; a file from a newer
version of Incidently is refused rather than guessed at.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .grading import debrief
from .session import Session

log = logging.getLogger(__name__)

# MIGRATIONS[n] upgrades a version-n file to version n + 1.
MIGRATIONS = (
    """
    CREATE TABLE sessions (
        id          TEXT PRIMARY KEY,
        scenario_id TEXT NOT NULL,
        title       TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        ended_at    TEXT,
        commands    INTEGER NOT NULL,
        hints       INTEGER NOT NULL,
        graded      INTEGER NOT NULL,
        total       INTEGER,
        maximum     INTEGER,
        percent     INTEGER,
        grade       TEXT,
        passed      INTEGER,
        data        TEXT NOT NULL
    );
    CREATE INDEX sessions_by_start ON sessions (started_at);
    """,
)

_SUMMARY = (
    "id, scenario_id, title, started_at, ended_at, commands, hints, "
    "graded, total, maximum, percent, grade, passed"
)
_COLUMNS = [*_SUMMARY.split(", "), "data"]
_UPSERT = (
    f"INSERT INTO sessions ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))}) "
    "ON CONFLICT (id) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "id")
)

AttemptStatus = Literal["in_progress", "ungraded", "passed", "failed"]
ProgressStatus = Literal["not_started", "in_progress", "attempted", "passed"]


class StorageError(Exception):
    """The database can't be opened, read, or written."""


@dataclass(frozen=True)
class Attempt:
    """One saved session, as the history lists it. Score fields are None until graded."""

    session_id: str
    scenario_id: str
    title: str
    started_at: str
    ended_at: str | None
    commands: int
    hints: int
    graded: bool
    total: int | None = None
    maximum: int | None = None
    percent: int | None = None
    grade: str | None = None
    passed: bool | None = None

    @property
    def status(self) -> AttemptStatus:
        if self.graded:
            return "passed" if self.passed else "failed"
        # Ended but not graded means grading failed; opening it retries.
        return "ungraded" if self.ended_at else "in_progress"

    @property
    def duration_seconds(self) -> int | None:
        """Time from start to submitting, once submitted."""
        if self.ended_at is None:
            return None
        started = datetime.fromisoformat(self.started_at)
        return max(0, int((datetime.fromisoformat(self.ended_at) - started).total_seconds()))


@dataclass(frozen=True)
class Progress:
    """A player's record on one scenario."""

    scenario_id: str
    status: ProgressStatus
    attempts: int
    best: Attempt | None  # the highest-scoring graded attempt
    resume_id: str | None  # the newest attempt not yet graded, to pick up again


@dataclass(frozen=True)
class Summary:
    """Totals across every attempt, for the progress dashboard."""

    attempts: int
    graded: int
    passed: int  # graded attempts that passed
    seconds_played: int  # summed over submitted attempts
    average_percent: int | None  # mean score of graded attempts

    @property
    def pass_rate(self) -> int | None:
        """Percentage of graded attempts that passed, or None before any were graded."""
        return round(100 * self.passed / self.graded) if self.graded else None


class Database:
    """Sessions by id in one SQLite file. Safe to share between threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._migrate()
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(f"can't open the session database at {path}: {exc}") from exc

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > len(MIGRATIONS):
            raise StorageError(
                f"{self.path} was written by a newer version of Incidently "
                f"(schema {version}; this version reads up to {len(MIGRATIONS)})"
            )
        for number in range(version, len(MIGRATIONS)):
            log.info("upgrading %s to schema %d", self.path, number + 1)
            self._conn.executescript(
                f"BEGIN; {MIGRATIONS[number]} PRAGMA user_version = {number + 1}; COMMIT;"
            )

    def save(self, session_id: str, session: Session) -> None:
        """Insert a session, or update it in place (its rowid, and so its order, stays put)."""
        score = debrief(session).score if session.assessment is not None else None
        row = (
            session_id,
            session.scenario.id,
            session.scenario.title,
            session.started_at,
            session.ended_at,
            session.commands_run,
            len(session.hints_given),
            score is not None,
            score.total if score else None,
            score.maximum if score else None,
            score.percent if score else None,
            score.grade if score else None,
            score.passed if score else None,
            session.model_dump_json(),
        )
        try:
            with self._lock, self._conn:
                self._conn.execute(_UPSERT, row)
        except sqlite3.Error as exc:
            raise StorageError(f"couldn't save session {session_id}: {exc}") from exc

    def load(self, session_id: str) -> Session | None:
        """The saved session, or None if there is no such id."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT data FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"couldn't read session {session_id}: {exc}") from exc
        if row is None:
            return None
        try:
            return Session.model_validate_json(row[0])
        except ValidationError as exc:
            raise StorageError(f"saved session {session_id} can't be read: {exc}") from exc

    def attempts(self) -> list[Attempt]:
        """Every saved session's summary, newest first."""
        try:
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT {_SUMMARY} FROM sessions ORDER BY started_at DESC, rowid DESC"
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"couldn't list saved sessions: {exc}") from exc
        return [_attempt(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def progress(attempts: Iterable[Attempt]) -> dict[str, Progress]:
    """Each attempted scenario's progress, from attempts listed newest first.

    The status is the best thing that has happened: passed, then in
    progress, then attempted. Scenarios never started are left out.
    """
    by_scenario: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        by_scenario.setdefault(attempt.scenario_id, []).append(attempt)

    result = {}
    for scenario_id, group in by_scenario.items():
        graded = [a for a in group if a.graded]
        best = max(graded, key=lambda a: (a.percent or 0, a.total or 0), default=None)
        resume_id = next((a.session_id for a in group if not a.graded), None)
        if any(a.passed for a in graded):
            status: ProgressStatus = "passed"
        elif resume_id is not None:
            status = "in_progress"
        else:
            status = "attempted"
        result[scenario_id] = Progress(scenario_id, status, len(group), best, resume_id)
    return result


def summarize(attempts: Iterable[Attempt]) -> Summary:
    attempts = list(attempts)
    graded = [a for a in attempts if a.graded]
    percents = [a.percent or 0 for a in graded]
    return Summary(
        attempts=len(attempts),
        graded=len(graded),
        passed=sum(1 for a in graded if a.passed),
        seconds_played=sum(a.duration_seconds or 0 for a in attempts),
        average_percent=round(sum(percents) / len(percents)) if percents else None,
    )


def _attempt(row: tuple) -> Attempt:
    (session_id, scenario_id, title, started_at, ended_at, commands, hints, graded, *score) = row
    total, maximum, percent, grade, passed = score
    return Attempt(
        session_id=session_id,
        scenario_id=scenario_id,
        title=title,
        started_at=started_at,
        ended_at=ended_at,
        commands=commands,
        hints=hints,
        graded=bool(graded),
        total=total,
        maximum=maximum,
        percent=percent,
        grade=grade,
        passed=None if passed is None else bool(passed),
    )
