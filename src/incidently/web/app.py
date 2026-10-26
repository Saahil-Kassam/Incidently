"""The local web app: a JSON API over the engine, plus five static pages.

Pages: ``/`` (scenario hub), ``/scenarios/<id>`` (one scenario and your
attempts), ``/progress`` (dashboard and history), ``/play/<session>``
(terminal), and ``/debrief/<session>`` (results). They are plain HTML and
ES modules, with xterm.js vendored under ``static/vendor``, so nothing
needs building and the app works offline.

Sessions are saved in SQLite (:mod:`incidently.storage`) after every change,
so they survive a restart. Those in use are cached in memory with a lock
each, so a second command sent while one is still being simulated gets
HTTP 409 instead of corrupting the state. The hub reads the history and
per-scenario progress from the same database.

Commands can be streamed: ``POST .../commands/stream`` answers with
newline-delimited JSON events (see :mod:`incidently.web.views`) so the
terminal shows output as the model writes it. The command runs on its own
thread and finishes even if the browser goes away, so its result is always
recorded in the session.

The app is meant for the machine it runs on. Requests must name a local host
(guarding against DNS rebinding), and state-changing requests from another
origin are refused (guarding against cross-site requests from other tabs),
because every command spends the user's API credits.
"""

from __future__ import annotations

import logging
import queue
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..catalog import Catalog, ScenarioError, instantiate
from ..config import Settings
from ..engine import Engine
from ..grading import debrief
from ..llm import LLMConfigError, LLMError, LLMRefusalError, LLMUnavailableError, StructuredLLM
from ..session import Session
from ..shell import MAX_COMMAND_LENGTH
from ..storage import Database, StorageError, progress
from .views import (
    AttemptView,
    DebriefView,
    ErrorEvent,
    HintView,
    OutputEvent,
    ProgressView,
    RestartEvent,
    ScenarioCard,
    ScenarioDetailView,
    SessionView,
    StatsView,
    TurnEvent,
    TurnView,
)

log = logging.getLogger(__name__)

STATIC_DIR = Path(str(resources.files("incidently.web") / "static"))
LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# --- requests -------------------------------------------------------------------


class NewSession(BaseModel):
    scenario_id: str
    seed: int | None = Field(default=None, ge=0, lt=2**32)


class CommandIn(BaseModel):
    # A little over the engine's limit so the engine, not validation, explains it.
    line: str = Field(max_length=MAX_COMMAND_LENGTH * 2)


# --- session store ---------------------------------------------------------------


@dataclass
class _Entry:
    id: str
    session: Session
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    """Sessions by unguessable id, saved in the database and cached once used.

    The cache keeps one entry (and so one lock) per session for the life of
    the server; the database is the source of truth.
    """

    def __init__(self, database: Database) -> None:
        self.database = database
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def add(self, session: Session) -> str:
        session_id = secrets.token_urlsafe(16)
        self.database.save(session_id, session)
        with self._lock:
            self._entries[session_id] = _Entry(session_id, session)
        return session_id

    def get(self, session_id: str) -> _Entry:
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                # Loaded while holding the lock, so two requests can't cache two copies.
                session = self.database.load(session_id)
                if session is None:
                    raise HTTPException(404, "No such session. Start a new one from the hub.")
                entry = self._entries[session_id] = _Entry(session_id, session)
        return entry

    def save(self, entry: _Entry) -> None:
        self.database.save(entry.id, entry.session)


def _acquire(entry: _Entry) -> None:
    if not entry.lock.acquire(blocking=False):
        raise HTTPException(409, "Still working on the previous request.")


@contextmanager
def _exclusive(store: SessionStore, entry: _Entry) -> Iterator[Session]:
    """Hold the session's lock, and save it on the way out, even after an error."""
    _acquire(entry)
    try:
        yield entry.session
    finally:
        try:
            store.save(entry)
        finally:
            entry.lock.release()


class _EventStream:
    """Events from the command thread, read by the response as NDJSON lines.

    It is also the engine's output listener, so streamed stdout goes straight
    into the queue.
    """

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[BaseModel | None] = queue.SimpleQueue()

    def write(self, text: str) -> None:
        self.put(OutputEvent(text=text))

    def restart(self) -> None:
        self.put(RestartEvent())

    def put(self, event: BaseModel) -> None:
        self._queue.put(event)

    def close(self) -> None:
        self._queue.put(None)

    def __iter__(self) -> Iterator[str]:
        # Starlette runs this blocking iterator on a worker thread.
        while (event := self._queue.get()) is not None:
            yield event.model_dump_json() + "\n"


class _RevalidatedStaticFiles(StaticFiles):
    """Static files the browser must check with the server before reusing.

    Without a Cache-Control header, browsers guess how long a file stays fresh
    from its age. After an upgrade that let a new page run with an old cached
    script, which broke the page. ``no-cache`` still caches, and the ETag makes
    each check a cheap 304.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _llm_failure(exc: LLMError) -> HTTPException:
    if isinstance(exc, LLMConfigError):
        return HTTPException(503, f"The model isn't configured: {exc}")
    if isinstance(exc, LLMUnavailableError):
        return HTTPException(503, "The model is busy or unreachable. Try again in a moment.")
    if isinstance(exc, LLMRefusalError):
        return HTTPException(502, "The model declined the request. Try again.")
    return HTTPException(502, "The model didn't return a usable reply. Try again.")


# --- app ------------------------------------------------------------------------


def create_app(
    settings: Settings,
    llm: StructuredLLM,
    *,
    catalog: Catalog | None = None,
    database: Database | None = None,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    """Build the app. ``database`` defaults to the file named by the settings."""
    catalog = catalog or Catalog.load()
    engine = Engine(llm, settings)
    store = SessionStore(database or Database(settings.database_path))

    app = FastAPI(title="Incidently", version=__version__, docs_url=None, redoc_url=None)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or LOCAL_HOSTS)

    @app.exception_handler(StorageError)
    async def storage_failure(request: Request, exc: StorageError) -> JSONResponse:
        log.error("storage failure: %s", exc)
        return JSONResponse({"detail": f"Saved sessions are unavailable: {exc}"}, status_code=500)

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method in _UNSAFE_METHODS
            and origin is not None
            and urlsplit(origin).netloc != request.headers.get("host")
        ):
            return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
        return await call_next(request)

    # --- API ---

    @app.get("/api/scenarios")
    def list_scenarios() -> list[ScenarioCard]:
        return [ScenarioCard.of(s) for s in catalog]

    @app.get("/api/scenarios/{scenario_id}")
    def scenario_detail(scenario_id: str) -> ScenarioDetailView:
        try:
            scenario = catalog.get(scenario_id)
        except ScenarioError:
            raise HTTPException(404, f"Unknown scenario {scenario_id!r}.") from None
        return ScenarioDetailView.of(scenario, store.database.attempts())

    @app.get("/api/stats")
    def stats() -> StatsView:
        return StatsView.of(catalog, store.database.attempts())

    @app.get("/api/progress")
    def scenario_progress() -> list[ProgressView]:
        records = progress(store.database.attempts())
        return [ProgressView.of(s.id, records.get(s.id)) for s in catalog]

    @app.get("/api/history")
    def history() -> list[AttemptView]:
        return [AttemptView.of(a) for a in store.database.attempts()]

    @app.post("/api/sessions", status_code=201)
    def start_session(body: NewSession) -> SessionView:
        try:
            scenario = catalog.get(body.scenario_id)
        except ScenarioError:
            raise HTTPException(404, f"Unknown scenario {body.scenario_id!r}.") from None
        try:
            session = engine.start(instantiate(scenario, seed=body.seed))
        except LLMError as exc:
            log.warning("session setup failed: %s", exc)
            raise _llm_failure(exc) from exc
        session_id = store.add(session)
        return SessionView.of(session_id, session, engine.prompt(session))

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> SessionView:
        session = store.get(session_id).session
        return SessionView.of(session_id, session, engine.prompt(session))

    @app.post("/api/sessions/{session_id}/commands")
    def run_command(session_id: str, body: CommandIn) -> TurnView:
        with _exclusive(store, store.get(session_id)) as session:
            return TurnView.of(engine.run(session, body.line))

    @app.post("/api/sessions/{session_id}/commands/stream")
    def stream_command(session_id: str, body: CommandIn) -> StreamingResponse:
        entry = store.get(session_id)
        _acquire(entry)
        events = _EventStream()

        def work() -> None:
            try:
                turn = engine.run(entry.session, body.line, output=events)
                store.save(entry)
                events.put(TurnEvent(turn=TurnView.of(turn)))
            except StorageError as exc:
                log.error("storage failure: %s", exc)
                events.put(ErrorEvent(detail=f"The command ran but couldn't be saved: {exc}"))
            except Exception:
                log.exception("command failed")
                events.put(ErrorEvent(detail="Something went wrong running that command."))
            finally:
                entry.lock.release()  # before the stream ends, so the next command isn't a 409
                events.close()

        threading.Thread(target=work, name="incidently-command", daemon=True).start()
        return StreamingResponse(
            events, media_type="application/x-ndjson", headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/sessions/{session_id}/hint")
    def next_hint(session_id: str) -> HintView:
        with _exclusive(store, store.get(session_id)) as session:
            hint = engine.hint(session)
        if hint is None:
            raise HTTPException(409, "No hints left for this incident.")
        return HintView.of(hint)

    @app.post("/api/sessions/{session_id}/finish")
    def finish(session_id: str) -> DebriefView:
        # Saved even if grading fails: the session has ended either way.
        with _exclusive(store, store.get(session_id)) as session:
            try:
                result = engine.finish(session)
            except LLMError as exc:
                log.warning("grading failed: %s", exc)
                raise _llm_failure(exc) from exc
        return DebriefView.of(session_id, session, result)

    @app.get("/api/sessions/{session_id}/debrief")
    def get_debrief(session_id: str) -> DebriefView:
        session = store.get(session_id).session
        if session.assessment is None:
            raise HTTPException(409, "This incident hasn't been graded yet.")
        return DebriefView.of(session_id, session, debrief(session))

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"version": __version__, "offline": settings.fake_llm, "model": settings.model}

    # --- pages ---

    def page(name: str) -> Response:
        return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    def hub_page() -> Response:
        return page("index.html")

    @app.get("/scenarios/{scenario_id}", include_in_schema=False)
    def scenario_page(scenario_id: str) -> Response:
        return page("scenario.html")

    @app.get("/progress", include_in_schema=False)
    def progress_page() -> Response:
        return page("progress.html")

    @app.get("/play/{session_id}", include_in_schema=False)
    def play_page(session_id: str) -> Response:
        return page("play.html")

    @app.get("/debrief/{session_id}", include_in_schema=False)
    def debrief_page(session_id: str) -> Response:
        return page("debrief.html")

    app.mount("/static", _RevalidatedStaticFiles(directory=STATIC_DIR), name="static")
    return app
