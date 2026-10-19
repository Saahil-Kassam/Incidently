import pytest

from incidently import engine as engine_module
from incidently.catalog import Catalog, instantiate
from incidently.config import Settings
from incidently.engine import Engine, Hint, _reveals_answer
from incidently.fake_llm import FakeLLM
from incidently.llm import (
    LLMConfigError,
    LLMRefusalError,
    LLMResponseError,
    LLMUnavailableError,
)
from incidently.offline import offline_reply
from incidently.replies import (
    Assessment,
    Bootstrap,
    CommandResult,
    CriterionAssessment,
    HintReply,
    StateUpdates,
)


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


def test_only_the_newest_message_carries_the_state(play):
    engine, llm, session = play
    llm.queue(reply("a\n", new_facts=["fact one"]), reply("b\n"))
    engine.run(session, "first")
    engine.run(session, "second")

    sent = llm.requests[-1].messages
    assert "<session_state>" not in sent[0]["content"]
    assert sent[0]["content"].startswith('<command host="jump01"')
    assert ">first</command>" in sent[0]["content"]
    assert "<session_state>" in sent[-1]["content"]
    assert "fact one" in sent[-1]["content"]  # state is current, not as of turn one


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


# --- hints --------------------------------------------------------------------


def test_hint_is_written_for_the_players_progress(play):
    engine, llm, session = play
    llm.queue(reply("ok\n"), HintReply(hint="  Look at the resolver's forwarders.  "))
    engine.run(session, "dig example.com")

    hint = engine.hint(session)
    assert hint == Hint(1, 1, "Look at the resolver's forwarders.", written=True)
    assert session.hints_given == ["Look at the resolver's forwarders."]

    request = llm.requests[-1]
    assert request.schema is HintReply
    assert request.effort == "low"
    content = request.messages[0]["content"]
    assert "#1 [netops@jump01 /home/netops] dig example.com" in content
    assert "This is hint 1 of 1." in content


def test_hints_run_out(play):
    engine, llm, session = play
    llm.queue(HintReply(hint="First."))
    assert engine.hint(session) is not None
    assert engine.hint(session) is None  # the lab scenario has one hint
    assert len(llm.requests) == 1


@pytest.mark.parametrize(
    "outcome",
    [LLMUnavailableError("busy"), HintReply(hint="   ")],
)
def test_hint_falls_back_to_the_scenarios_own(play, outcome):
    engine, llm, session = play
    llm.queue(outcome)
    hint = engine.hint(session)
    assert hint.text == session.scenario.hints[0]
    assert hint.written is False
    assert session.hints_given == [session.scenario.hints[0]]


def test_hint_quoting_the_root_cause_is_replaced(play):
    engine, llm, session = play
    leaked = f"Honestly: {session.root_cause.lower()} Go fix it."
    llm.queue(HintReply(hint=leaked))
    assert engine.hint(session).text == session.scenario.hints[0]


def test_hint_paraphrasing_is_allowed(play):
    engine, llm, session = play
    llm.queue(HintReply(hint="Check where the resolver sends queries it can't answer."))
    assert engine.hint(session).written


def test_long_secret_needs_a_long_quoted_run(session):
    session.root_cause = (
        "The forwarder on the resolver points at an upstream that stopped answering"
    )
    assert not _reveals_answer("Is the forwarder on the resolver healthy?", session)
    assert _reveals_answer(
        "the forwarder on the resolver points at an upstream that stopped", session
    )


def test_empty_hidden_text_never_counts_as_leaked(session):
    session.root_cause = "..."
    assert not _reveals_answer("Any hint at all.", session)


# --- finishing ------------------------------------------------------------------


def graded(**overrides):
    fields = {
        "criteria": [
            CriterionAssessment(id="find_cause", met=True, turn=1, feedback="Found it."),
            CriterionAssessment(id="verify_fix", met=False, turn=0, feedback="Not verified."),
        ],
        "key_evidence_turn": 1,
        "resolved": True,
        "resolved_turn": 1,
        "risk_events": [],
        "summary": "Solid work.",
        "root_cause_explanation": "The forwarder was dead.",
        "strengths": ["Checked the logs first."],
        "improvements": ["Verify the fix."],
        "ideal_path": ["dig example.com"],
    }
    return Assessment(**(fields | overrides))


def test_finish_grades_once_and_returns_the_debrief(play):
    engine, llm, session = play
    llm.queue(reply("ok\n"), graded())
    engine.run(session, "dig example.com")

    d = engine.finish(session)
    assert session.ended
    assert d.score.earned == 60
    assert d.score.passed
    assert d.summary == "Solid work."

    request = llm.requests[-1]
    assert request.schema is Assessment
    assert request.effort == "high"
    content = request.messages[0]["content"]
    assert "Grading criteria" in content
    assert "dig example.com   (exit 0)" in content

    assert engine.finish(session) == d  # no second grading call
    assert len(llm.requests) == 2


def test_failed_grading_can_be_retried(play):
    engine, llm, session = play
    llm.queue(LLMUnavailableError("busy"))
    with pytest.raises(LLMUnavailableError):
        engine.finish(session)
    assert session.ended
    assert session.assessment is None

    llm.queue(graded())
    assert engine.finish(session).summary == "Solid work."


def test_an_ended_session_takes_no_more_commands_or_hints(play):
    engine, llm, session = play
    llm.queue(graded())
    engine.finish(session)

    turn = engine.run(session, "dig example.com")
    assert not turn.ok
    assert "ended" in turn.notice
    assert engine.hint(session) is None
    assert len(llm.requests) == 1


def test_offline_session_can_hint_and_finish(settings, lab_instance):
    engine = Engine(FakeLLM(default=offline_reply), settings)
    session = engine.start(lab_instance)
    engine.run(session, "dig example.com")

    assert engine.hint(session).text == session.scenario.hints[0]
    d = engine.finish(session)
    assert d.score.total == 0
    assert "not graded" in d.summary


def test_setup_and_simulation_share_the_session_clock(settings, lab_instance, bootstrap):
    llm = FakeLLM(bootstrap, reply())
    engine = Engine(llm, settings)
    session = engine.start(lab_instance)
    engine.run(session, "date")

    assert (
        f"Session start time (UTC): {session.started_at}" in llm.requests[0].messages[0]["content"]
    )
    assert f"The session started at {session.started_at} (UTC)" in llm.requests[1].system


def test_history_keeps_prompts_and_local_output_for_redrawing(play):
    engine, llm, session = play
    llm.queue(reply(connect_to_host="core-rtr1"), reply("Gi0/1 up\n"))
    engine.run(session, "ssh core-rtr1")
    engine.run(session, "show ip int brief")
    engine.run(session, "exit")
    engine.run(session, "history")

    assert [(r.prompt, r.command) for r in session.history] == [
        ("netops@jump01:~$ ", "ssh core-rtr1"),
        ("core-rtr1#", "show ip int brief"),
        ("core-rtr1#", "exit"),
        ("netops@jump01:~$ ", "history"),
    ]
    assert session.history[1].stdout == "Gi0/1 up\n"
    assert session.history[2].stdout == "Connection to core-rtr1 closed.\n"
    assert session.history[3].stdout.startswith("    1  ssh core-rtr1\n")
