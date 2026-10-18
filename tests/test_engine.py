import pytest

from incidently import engine as engine_module
from incidently.catalog import Catalog, instantiate
from incidently.config import Settings
from incidently.engine import Engine
from incidently.fake_llm import FakeLLM
from incidently.llm import (
    LLMConfigError,
    LLMRefusalError,
    LLMResponseError,
    LLMUnavailableError,
)
from incidently.offline import offline_reply
from incidently.replies import Bootstrap, CommandResult, StateUpdates


def reply(stdout="", stderr="", exit_code=0, **updates):
    fields = {
        "cwd": "",
        "user": "",
        "connect_to_host": "",
        "cli_mode": "",
        "services": [],
        "new_facts": [],
    }
    return CommandResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        state_updates=StateUpdates(**(fields | updates)),
    )


@pytest.fixture
def settings():
    return Settings(turn_effort="low", deep_effort="high")


@pytest.fixture
def play(settings, session):
    """An engine over a scripted fake, and a session already started."""
    llm = FakeLLM()
    return Engine(llm, settings), llm, session


# --- start ------------------------------------------------------------------


def test_start_bootstraps_with_deep_effort(settings, lab_instance, bootstrap):
    llm = FakeLLM(bootstrap)
    session = Engine(llm, settings).start(lab_instance)

    assert session.ticket == bootstrap.ticket
    assert session.facts == bootstrap.facts
    (request,) = llm.requests
    assert request.schema is Bootstrap
    assert request.effort == "high"
    content = request.messages[0]["content"]
    assert lab_instance.root_cause in content
    assert "Grading criteria" in content  # setup sees the full scenario


def test_start_failure_propagates(settings, lab_instance):
    with pytest.raises(LLMUnavailableError):
        Engine(FakeLLM(LLMUnavailableError("down")), settings).start(lab_instance)


# --- simulated commands -----------------------------------------------------


def test_command_is_simulated_and_recorded(play):
    engine, llm, session = play
    llm.queue(reply("10.0.0.10\n", new_facts=["eth0 is 10.0.0.10/24"]))

    turn = engine.run(session, "  hostname -I  ")

    assert turn.ok
    assert turn.stdout == "10.0.0.10\n"
    assert turn.exit_code == 0
    assert turn.prompt == "netops@jump01:~$ "
    assert session.history[-1].command == "hostname -I"
    assert session.history[-1].model == "fake-llm"
    assert "eth0 is 10.0.0.10/24" in session.facts


def test_command_request_carries_state_and_low_effort(play):
    engine, llm, session = play
    llm.queue(reply())
    engine.run(session, "ip route")

    request = llm.requests[-1]
    assert request.schema is CommandResult
    assert request.effort == "low"
    content = request.messages[-1]["content"]
    assert '"active_host":"jump01"' in content
    assert 'host="jump01" shell="bash"' in content
    assert ">ip route</command>" in content


def test_simulation_prompt_has_the_root_cause_but_not_the_rubric(play):
    engine, llm, session = play
    llm.queue(reply())
    engine.run(session, "ip route")

    system = llm.requests[-1].system
    assert session.root_cause in system
    assert session.ticket.description in system
    assert "Grading criteria" not in system
    assert session.scenario.hidden_flag not in system


def test_system_prompt_is_stable_across_commands(play):
    engine, llm, session = play
    llm.queue(reply(cwd="/var/log"), reply(connect_to_host="core-rtr1"), reply())
    for command in ("cd /var/log", "ssh core-rtr1", "show ip route"):
        engine.run(session, command)
    assert len({r.system for r in llm.requests}) == 1


def test_conversation_grows_with_each_command(play):
    engine, llm, session = play
    llm.queue(reply("a\n"), reply("b\n"))
    engine.run(session, "first")
    engine.run(session, "second")

    assert len(llm.requests[-1].messages) == 3  # first exchange + new command
    assert [m["role"] for m in session.messages] == ["user", "assistant"] * 2
    assert CommandResult.model_validate_json(session.messages[1]["content"]).stdout == "a\n"


def test_state_updates_change_the_prompt(play):
    engine, llm, session = play
    llm.queue(reply(connect_to_host="core-rtr1"), reply(cli_mode="config"))
    assert engine.run(session, "ssh core-rtr1").prompt == "core-rtr1#"
    assert engine.run(session, "configure terminal").prompt == "core-rtr1(config)#"


@pytest.mark.parametrize(
    ("error", "notice"),
    [
        (LLMConfigError("no key"), "can't reach the model"),
        (LLMUnavailableError("429"), "busy"),
        (LLMRefusalError("cyber"), "declined"),
        (LLMResponseError("bad json"), "couldn't produce output"),
    ],
)
def test_model_failure_leaves_the_session_unchanged(play, error, notice):
    engine, llm, session = play
    llm.queue(error)
    turn = engine.run(session, "ip route")

    assert not turn.ok
    assert notice in turn.notice
    assert session.history == []
    assert session.messages == []


def test_old_turns_are_trimmed_in_chunks(play, monkeypatch):
    monkeypatch.setattr(engine_module, "HISTORY_TURNS", 4)
    monkeypatch.setattr(engine_module, "TRIM_TURNS", 2)
    engine, llm, session = play

    counts = []
    for n in range(8):
        llm.queue(reply(f"{n}\n"))
        engine.run(session, f"echo {n}")
        counts.append(len(session.messages) // 2)

    assert counts == [1, 2, 3, 4, 3, 4, 3, 4]
    assert session.messages[0]["role"] == "user"
    assert len(session.history) == 8  # the full history is kept for grading


# --- local commands ---------------------------------------------------------


def test_local_commands_never_call_the_model(play):
    engine, llm, session = play
    for line in ("", "clear", "history", "vim /etc/hosts", "exit"):
        engine.run(session, line)
    assert llm.requests == []


def test_empty_line_is_not_recorded(play):
    engine, _, session = play
    turn = engine.run(session, "   ")
    assert turn.ok and turn.stdout == ""
    assert session.history == []


def test_clear_is_recorded_and_clears(play):
    engine, _, session = play
    turn = engine.run(session, "clear")
    assert turn.clear_screen
    assert session.history[-1].local


def test_history_lists_commands_including_itself(play):
    engine, llm, session = play
    llm.queue(reply())
    engine.run(session, "ip a")
    turn = engine.run(session, "history")
    assert turn.stdout == "    1  ip a\n    2  history\n"


def test_blocked_command_explains_and_is_not_recorded(play):
    engine, _, session = play
    turn = engine.run(session, "nano /etc/hosts")
    assert not turn.ok
    assert "sed -i" in turn.notice
    assert session.history == []


def test_exit_on_the_first_host_asks_to_end(play):
    engine, _, session = play
    turn = engine.run(session, "exit")
    assert turn.end_requested
    assert session.history == []


def test_exit_from_a_remote_session_returns(play):
    engine, llm, session = play
    llm.queue(reply(connect_to_host="core-rtr1"))
    engine.run(session, "ssh core-rtr1")

    turn = engine.run(session, "exit")
    assert turn.stdout == "Connection to core-rtr1 closed.\n"
    assert turn.prompt == "netops@jump01:~$ "
    assert not turn.end_requested
    assert session.history[-1].host == "core-rtr1"  # recorded where it was typed


def test_cisco_end_leaves_config_mode_locally(play):
    engine, llm, session = play
    llm.queue(reply(connect_to_host="core-rtr1"), reply(cli_mode="config-if"))
    engine.run(session, "ssh core-rtr1")
    engine.run(session, "interface Gi0/1")

    turn = engine.run(session, "end")
    assert turn.prompt == "core-rtr1#"
    assert len(llm.requests) == 2


# --- offline mode -----------------------------------------------------------


def test_full_offline_session(settings, lab_instance):
    engine = Engine(FakeLLM(default=offline_reply), settings)
    session = engine.start(lab_instance)
    assert session.ticket.id == "INC0000000"

    prompts = [
        engine.run(session, line).prompt
        for line in ("cd /var/log", "ssh netops@core-rtr1", "conf t", "interface Gi0/1", "end")
    ]
    assert prompts == [
        "netops@jump01:/var/log$ ",
        "core-rtr1#",
        "core-rtr1(config)#",
        "core-rtr1(config-if)#",
        "core-rtr1#",
    ]
    assert "[offline]" in engine.run(session, "show ip route").stdout


@pytest.mark.parametrize("scenario_id", sorted(s.id for s in Catalog.load()))
def test_every_bundled_scenario_plays_offline(settings, scenario_id):
    engine = Engine(FakeLLM(default=offline_reply), settings)
    session = engine.start(instantiate(Catalog.load().get(scenario_id), seed=1))
    turn = engine.run(session, "whoami")
    assert turn.ok
    assert turn.prompt
