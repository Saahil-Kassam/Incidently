"""The web API, driven through FastAPI's test client with a fake model."""

import json

import pytest
from starlette.testclient import TestClient

from incidently import __main__ as cli
from incidently.catalog import Catalog
from incidently.config import Settings
from incidently.engine import Engine
from incidently.fake_llm import FakeLLM
from incidently.llm import LLMConfigError, LLMRefusalError, LLMResponseError, LLMUnavailableError
from incidently.offline import offline_reply
from incidently.replies import (
    Assessment,
    CommandResult,
    CriterionAssessment,
    HintReply,
    StateUpdates,
)
from incidently.storage import Database, StorageError
from incidently.web.app import create_app

LAB = "lab-outage"


def reply(stdout="", **updates):
    fields = {
        "cwd": "",
        "user": "",
        "connect_to_host": "",
        "cli_mode": "",
        "services": [],
        "new_facts": [],
    }
    return CommandResult(
        stdout=stdout, stderr="", exit_code=0, state_updates=StateUpdates(**(fields | updates))
    )


def graded():
    return Assessment(
        criteria=[CriterionAssessment(id="find_cause", met=True, turn=1, feedback="Found it.")],
        key_evidence_turn=1,
        resolved=True,
        resolved_turn=1,
        risk_events=[],
        summary="Good work.",
        root_cause_explanation="The forwarder was dead.",
        strengths=["Checked logs."],
        improvements=["Verify sooner."],
        ideal_path=["dig example.com"],
    )


@pytest.fixture
def catalog(write_scenario, tmp_path):
    from conftest import LAB_SCENARIO

    write_scenario(LAB_SCENARIO)
    return Catalog.load(tmp_path)


@pytest.fixture
def llm(bootstrap):
    return FakeLLM(bootstrap)


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


def make_client(llm, catalog, database):
    app = create_app(
        Settings(), llm, catalog=catalog, database=database, allowed_hosts=["testserver"]
    )
    return TestClient(app)


@pytest.fixture
def client(llm, catalog, database):
    return make_client(llm, catalog, database)


@pytest.fixture
def restart(llm, catalog, database):
    """A fresh app over the same database file, as after restarting the server."""

    def restarted():
        return make_client(llm, catalog, Database(database.path))

    return restarted


@pytest.fixture
def session_id(client):
    response = client.post("/api/sessions", json={"scenario_id": LAB, "seed": 1})
    assert response.status_code == 201
    return response.json()["id"]


def url(session_id, action=""):
    return f"/api/sessions/{session_id}" + (f"/{action}" if action else "")


def stream(client, session_id, line):
    """Run a command through the streaming endpoint and return its events."""
    response = client.post(url(session_id, "commands/stream"), json={"line": line})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/x-ndjson"
    return [json.loads(event) for event in response.text.splitlines()]


# --- scenarios and sessions --------------------------------------------------------


def test_lists_scenarios(client):
    (card,) = client.get("/api/scenarios").json()
    assert card == {
        "id": LAB,
        "title": "Test Outage",
        "category": "Networking",
        "topic": "DNS",
        "difficulty": "beginner",
        "summary": "Users can't resolve names.",
    }


def test_start_session_returns_the_player_view(client, bootstrap):
    body = client.post("/api/sessions", json={"scenario_id": LAB}).json()
    assert len(body["id"]) >= 20
    assert body["ticket"]["id"] == bootstrap.ticket.id
    assert body["prompt"] == "netops@jump01:~$ "
    assert [h["name"] for h in body["hosts"]] == ["jump01", "core-rtr1", "dc01"]
    assert body["hints"] == [] and body["hints_total"] == 1
    assert body["transcript"] == []
    assert not body["ended"] and not body["graded"]


def test_unknown_scenario_is_404(client):
    assert client.post("/api/sessions", json={"scenario_id": "nope"}).status_code == 404


@pytest.mark.parametrize("body", [{}, {"scenario_id": LAB, "seed": -1}, {"scenario_id": 3}])
def test_invalid_start_request_is_422(client, body):
    assert client.post("/api/sessions", json=body).status_code == 422


@pytest.mark.parametrize(
    ("error", "status", "text"),
    [
        (LLMConfigError("no key"), 503, "isn't configured"),
        (LLMUnavailableError("down"), 503, "busy or unreachable"),
        (LLMRefusalError("no"), 502, "declined"),
        (LLMResponseError("junk"), 502, "usable reply"),
    ],
)
def test_setup_failures_become_clear_http_errors(catalog, error, status, text):
    app = create_app(Settings(), FakeLLM(error), catalog=catalog, allowed_hosts=["testserver"])
    response = TestClient(app).post("/api/sessions", json={"scenario_id": LAB})
    assert response.status_code == status
    assert text in response.json()["detail"]


def test_unknown_session_is_404(client):
    for method, action in [
        ("get", ""),
        ("post", "commands"),
        ("post", "commands/stream"),
        ("post", "hint"),
        ("post", "finish"),
    ]:
        kwargs = {"json": {"line": "ls"}} if action.startswith("commands") else {}
        assert getattr(client, method)(url("nope", action), **kwargs).status_code == 404


# --- commands --------------------------------------------------------------------


def test_command_round_trip_and_transcript(client, llm, session_id):
    llm.queue(reply("10.0.0.10\n", connect_to_host="core-rtr1"))
    turn = client.post(url(session_id, "commands"), json={"line": "ssh core-rtr1"}).json()
    assert turn == {
        "prompt": "core-rtr1#",
        "stdout": "10.0.0.10\n",
        "stderr": "",
        "exit_code": 0,
        "clear_screen": False,
        "end_requested": False,
        "notice": "",
        "ok": True,
    }
    view = client.get(url(session_id)).json()
    assert view["prompt"] == "core-rtr1#"
    assert view["transcript"] == [
        {
            "prompt": "netops@jump01:~$ ",
            "command": "ssh core-rtr1",
            "stdout": "10.0.0.10\n",
            "stderr": "",
        }
    ]


def test_exit_on_first_host_requests_the_end(client, session_id):
    assert client.post(url(session_id, "commands"), json={"line": "exit"}).json()["end_requested"]


def test_overlong_command_is_rejected(client, session_id):
    assert client.post(url(session_id, "commands"), json={"line": "x" * 5000}).status_code == 422


@pytest.mark.parametrize("action", ["commands", "commands/stream"])
def test_concurrent_request_on_a_busy_session_is_409(client, session_id, action):
    entry = client.app.state.store.get(session_id)
    with entry.lock:
        response = client.post(url(session_id, action), json={"line": "ls"})
    assert response.status_code == 409


# --- streamed commands -----------------------------------------------------------


def test_streamed_command_sends_output_then_the_turn(client, llm, session_id):
    stdout = "PING 10.0.0.1 56(84) bytes of data.\n64 bytes from 10.0.0.1: icmp_seq=1 ttl=64\n"
    llm.queue(reply(stdout, new_facts=["SECRET-FACT"]))

    *outputs, last = stream(client, session_id, "ping -c1 10.0.0.1")

    assert len(outputs) > 1
    assert {e["type"] for e in outputs} == {"output"}
    assert "".join(e["text"] for e in outputs) == stdout
    assert last["type"] == "turn"
    assert last["turn"]["stdout"] == stdout and last["turn"]["ok"]
    assert client.get(url(session_id)).json()["transcript"][-1]["stdout"] == stdout


def test_streamed_local_command_sends_only_the_turn(client, session_id):
    (event,) = stream(client, session_id, "history")
    assert event["type"] == "turn"
    assert "history" in event["turn"]["stdout"]


def test_streamed_command_failure_is_a_notice(client, llm, session_id):
    llm.queue(LLMUnavailableError("down"))
    (event,) = stream(client, session_id, "ip route")
    assert not event["turn"]["ok"]
    assert "busy" in event["turn"]["notice"]


def test_unexpected_error_is_an_error_event_and_frees_the_session(
    client, llm, session_id, monkeypatch
):
    def broken(self, session, line, output=None):
        raise RuntimeError("bug")

    with monkeypatch.context() as m:
        m.setattr(Engine, "run", broken)
        (event,) = stream(client, session_id, "ls")
    assert event == {"type": "error", "detail": "Something went wrong running that command."}

    llm.queue(reply("file\n"))
    assert stream(client, session_id, "ls")[-1]["turn"]["stdout"] == "file\n"


def test_cross_origin_streamed_commands_are_refused(client, session_id):
    response = client.post(
        url(session_id, "commands/stream"),
        json={"line": "ls"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403


# --- hints and grading -------------------------------------------------------------


def test_hint_then_none_left(client, llm, session_id):
    llm.queue(HintReply(hint="Look at the forwarders."))
    assert client.post(url(session_id, "hint")).json() == {
        "number": 1,
        "total": 1,
        "text": "Look at the forwarders.",
    }
    assert client.get(url(session_id)).json()["hints"] == ["Look at the forwarders."]
    response = client.post(url(session_id, "hint"))
    assert response.status_code == 409


def test_finish_returns_the_debrief_and_reveals_the_root_cause(client, llm, session_id, catalog):
    llm.queue(reply("ok\n"), graded())
    client.post(url(session_id, "commands"), json={"line": "dig example.com"})

    assert client.get(url(session_id, "debrief")).status_code == 409  # not graded yet
    debrief = client.post(url(session_id, "finish")).json()
    assert debrief["score"]["passed"] is True
    assert debrief["root_cause"] in catalog.get(LAB).root_cause_variants
    assert debrief["checkpoints"][0]["met"] is True
    assert client.get(url(session_id, "debrief")).json() == debrief
    assert client.get(url(session_id)).json()["graded"] is True

    after = client.post(url(session_id, "commands"), json={"line": "ls"}).json()
    assert not after["ok"] and "ended" in after["notice"]


def test_failed_grading_can_be_retried(client, llm, session_id):
    llm.queue(LLMUnavailableError("down"))
    assert client.post(url(session_id, "finish")).status_code == 503
    assert client.get(url(session_id)).json()["ended"] is True
    llm.queue(graded())
    assert client.post(url(session_id, "finish")).status_code == 200


def test_nothing_secret_reaches_the_browser_before_grading(catalog, bootstrap, tmp_path):
    """Play a whole offline session and scan every response, streamed ones too, for hidden text."""
    llm = FakeLLM(bootstrap, default=offline_reply)
    client = make_client(llm, catalog, Database(tmp_path / "leak.db"))
    scenario = catalog.get(LAB)

    responses = [client.get("/api/scenarios"), client.get("/api/health")]
    start = client.post("/api/sessions", json={"scenario_id": LAB, "seed": 3})
    responses.append(start)
    sid = start.json()["id"]
    for line in ("dig example.com", "ssh core-rtr1", "conf t", "end", "history", "exit"):
        responses.append(client.post(url(sid, "commands"), json={"line": line}))
    streamed = [
        client.post(url(sid, "commands/stream"), json={"line": line}).text
        for line in ("cat /etc/resolv.conf", "ssh dc01", "Get-DnsServerForwarder")
    ]
    responses += [client.post(url(sid, "hint")), client.get(url(sid))]
    responses += [client.get("/api/history"), client.get("/api/progress")]

    text = "\n".join([*(json.dumps(r.json()) for r in responses), *streamed])
    secrets = [
        *scenario.root_cause_variants,
        scenario.hidden_flag,
        scenario.resolution_condition,
        scenario.simulation_notes,
        *(c.description for c in scenario.grading),
        *(c.id for c in scenario.grading),
        *bootstrap.facts,
    ]
    for secret in secrets:
        assert json.dumps(secret)[1:-1] not in text, secret


# --- request checks -----------------------------------------------------------------


def test_requests_for_other_hosts_are_refused(llm, catalog):
    client = TestClient(
        create_app(Settings(), llm, catalog=catalog), base_url="http://evil.example"
    )
    assert client.get("/api/scenarios").status_code == 400


def test_localhost_is_allowed_by_default(llm, catalog):
    client = TestClient(
        create_app(Settings(), llm, catalog=catalog), base_url="http://127.0.0.1:8000"
    )
    assert client.get("/api/scenarios").status_code == 200


def test_cross_origin_posts_are_refused(client, session_id):
    response = client.post(url(session_id, "hint"), headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_same_origin_posts_are_allowed(client, llm, session_id):
    llm.queue(HintReply(hint="h"))
    response = client.post(url(session_id, "hint"), headers={"Origin": "http://testserver"})
    assert response.status_code == 200


# --- saved sessions -------------------------------------------------------------------


def test_sessions_survive_a_restart(client, llm, session_id, restart):
    llm.queue(reply("10.0.0.10\n", connect_to_host="core-rtr1"))
    stream(client, session_id, "ssh core-rtr1")
    before = client.get(url(session_id)).json()

    again = restart()
    assert again.get(url(session_id)).json() == before
    llm.queue(reply("Router uptime is 3 days\n"))
    turn = again.post(url(session_id, "commands"), json={"line": "show version"}).json()
    assert turn["prompt"] == "core-rtr1#"  # still on the host it was left on


def test_every_change_is_saved(client, llm, session_id, database):
    def saved():
        return database.load(session_id)

    llm.queue(reply("ok\n"))
    client.post(url(session_id, "commands"), json={"line": "dig example.com"})
    assert saved().turn == 1
    stream(client, session_id, "history")
    assert saved().turn == 2
    llm.queue(HintReply(hint="Look at the forwarders."))
    client.post(url(session_id, "hint"))
    assert saved().hints_given == ["Look at the forwarders."]
    llm.queue(graded())
    client.post(url(session_id, "finish"))
    assert saved().assessment is not None


def test_failed_grading_is_saved_as_ended(client, llm, session_id, restart):
    llm.queue(LLMUnavailableError("down"))
    assert client.post(url(session_id, "finish")).status_code == 503
    assert restart().get(url(session_id)).json()["ended"] is True


def test_a_graded_debrief_reopens_after_a_restart(client, llm, session_id, restart):
    llm.queue(reply("ok\n"), graded())
    client.post(url(session_id, "commands"), json={"line": "dig example.com"})
    debrief = client.post(url(session_id, "finish")).json()
    assert restart().get(url(session_id, "debrief")).json() == debrief


def test_one_cached_entry_and_lock_per_session(client, session_id, restart):
    store = restart().app.state.store
    assert store.get(session_id) is store.get(session_id)


def test_storage_failures_are_clear_errors(client, session_id, restart, monkeypatch):
    again = restart()

    def broken(session_id):
        raise StorageError("disk on fire")

    monkeypatch.setattr(again.app.state.store.database, "load", broken)
    response = again.get(url(session_id))
    assert response.status_code == 500
    assert "disk on fire" in response.json()["detail"]


def test_default_database_is_in_the_data_folder(llm, catalog, isolated_data_dir):
    app = create_app(Settings(), llm, catalog=catalog, allowed_hosts=["testserver"])
    TestClient(app).post("/api/sessions", json={"scenario_id": LAB})
    assert (isolated_data_dir / "incidently.db").exists()


# --- history and progress ----------------------------------------------------------------


def test_history_lists_attempts_newest_first(client, llm, session_id, bootstrap):
    llm.queue(reply("ok\n"), graded())
    client.post(url(session_id, "commands"), json={"line": "dig example.com"})
    client.post(url(session_id, "finish"))
    llm.queue(bootstrap)
    newer = client.post("/api/sessions", json={"scenario_id": LAB}).json()["id"]

    first, second = client.get("/api/history").json()
    assert first["session_id"] == newer
    assert first["status"] == "in_progress"
    assert first["total"] is None and first["duration_seconds"] is None
    assert second["session_id"] == session_id
    assert second["status"] == "passed"
    assert second["title"] == "Test Outage"
    assert second["commands"] == 1 and second["hints"] == 0
    assert second["grade"] and second["percent"] > 0
    assert second["duration_seconds"] >= 0


def test_progress_for_an_unplayed_scenario(client):
    assert client.get("/api/progress").json() == [
        {
            "scenario_id": LAB,
            "status": "not_started",
            "attempts": 0,
            "best_percent": None,
            "best_grade": None,
            "resume_id": None,
        }
    ]


def test_progress_offers_to_resume_an_unfinished_attempt(client, session_id):
    (record,) = client.get("/api/progress").json()
    assert record["status"] == "in_progress"
    assert record["resume_id"] == session_id


def test_progress_after_passing(client, llm, session_id):
    llm.queue(reply("ok\n"), graded())
    client.post(url(session_id, "commands"), json={"line": "dig example.com"})
    client.post(url(session_id, "finish"))

    (record,) = client.get("/api/progress").json()
    assert record["status"] == "passed"
    assert record["attempts"] == 1
    assert record["best_percent"] > 0 and record["best_grade"]
    assert record["resume_id"] is None


# --- pages ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("/", "hub.js"),
        ("/play/abc", "play.js"),
        ("/debrief/abc", "debrief.js"),
        ("/static/style.css", "--accent"),
        ("/static/vendor/xterm/xterm.js", "Terminal"),
        ("/static/vendor/xterm/addon-fit.js", "FitAddon"),
    ],
)
def test_pages_and_assets_are_served(client, path, marker):
    response = client.get(path)
    assert response.status_code == 200
    assert marker in response.text


def test_health_reports_mode(client):
    assert client.get("/api/health").json()["offline"] is False


# --- command line --------------------------------------------------------------------


def test_cli_starts_uvicorn_on_localhost(monkeypatch, capsys, tmp_path, isolated_data_dir):
    monkeypatch.chdir(tmp_path)  # don't read the developer's real .env
    calls = {}
    monkeypatch.setenv("INCIDENTLY_FAKE_LLM", "1")
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: calls.update(app=app, **kw))
    assert cli.main(["--no-browser", "--port", "8123"]) == 0
    assert calls["host"] == "127.0.0.1" and calls["port"] == 8123
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8123/" in out
    assert str(isolated_data_dir / "incidently-offline.db") in out


def test_cli_reports_an_unusable_data_folder(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INCIDENTLY_FAKE_LLM", "1")
    (tmp_path / "blocked").write_text("a file, not a folder")
    monkeypatch.setenv("INCIDENTLY_DATA_DIR", str(tmp_path / "blocked"))
    assert cli.main(["--no-browser"]) == 2
    assert "can't open the session database" in capsys.readouterr().err


def test_cli_reports_bad_configuration(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INCIDENTLY_TURN_EFFORT", "extreme")
    assert cli.main(["--no-browser"]) == 2
    assert "INCIDENTLY_TURN_EFFORT" in capsys.readouterr().err
