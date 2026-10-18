from incidently.replies import CommandResult, ServiceChange, StateUpdates
from incidently.session import MAX_RECORDED_OUTPUT, Session


def result(stdout="", **updates):
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


def test_create_starts_on_the_start_host(session, lab_instance):
    assert session.active.name == "jump01"
    assert session.chain == ["jump01"]
    assert session.root_cause == lab_instance.root_cause
    assert session.seed == 7
    assert session.ticket.id == "INC0012345"
    assert session.facts == ["Resolver is unbound 1.19.2", "Upstream forwarder is 192.0.2.53"]
    assert session.turn == 0


def test_cisco_hosts_start_in_exec_mode(session):
    assert session.hosts["core-rtr1"].cli_mode == "exec"
    assert session.hosts["jump01"].cli_mode == ""


def test_record_captures_where_the_command_ran(session):
    entry = session.record("ip a", result("eth0 ...\n"), model="claude-opus-5-5")
    assert (entry.turn, entry.host, entry.user, entry.cwd) == (
        1,
        "jump01",
        "netops",
        "/home/netops",
    )
    assert entry.stdout == "eth0 ...\n"
    assert entry.exit_code == 0
    assert entry.model == "claude-opus-5-5"
    assert entry.local is False


def test_local_commands_are_marked(session):
    entry = session.record("clear")
    assert entry.local is True
    assert entry.exit_code is None


def test_recorded_output_is_capped(session):
    entry = session.record("cat big.log", result("x" * (MAX_RECORDED_OUTPUT + 500)))
    assert len(entry.stdout) == MAX_RECORDED_OUTPUT


def test_apply_changes_user_and_cwd(session):
    session.apply(result(user="root", cwd="/etc/unbound"))
    assert session.active.user == "root"
    assert session.active.cwd == "/etc/unbound"


def test_relative_bash_paths_are_resolved(session):
    session.apply(result(cwd="../../var/log"))
    assert session.active.cwd == "/var/log"


def test_connect_moves_into_an_inventory_host(session):
    session.apply(result(connect_to_host="CORE-RTR1"))  # case-insensitive match
    assert session.chain == ["jump01", "core-rtr1"]
    assert session.remote


def test_connect_to_an_unknown_host_is_ignored(session):
    session.apply(result(connect_to_host="evil-host"))
    assert session.chain == ["jump01"]


def test_cli_mode_only_applies_to_cisco(session):
    session.apply(result(cli_mode="config"))
    assert session.active.cli_mode == ""
    session.apply(result(connect_to_host="core-rtr1"))
    session.apply(result(cli_mode="config"))
    assert session.active.cli_mode == "config"


def test_services_are_keyed_by_inventory_host(session):
    changes = [
        ServiceChange(host="dc01", name="DNS", status="stopped"),
        ServiceChange(host="nope", name="unbound", status="running"),  # falls back to active
    ]
    session.apply(result(services=changes))
    assert session.services == {"dc01:DNS": "stopped", "jump01:unbound": "running"}


def test_new_facts_are_added_once(session):
    session.apply(result(new_facts=["PID of unbound is 812", "Resolver is unbound 1.19.2", " "]))
    assert session.facts[-1] == "PID of unbound is 812"
    assert session.facts.count("Resolver is unbound 1.19.2") == 1


def test_disconnect_returns_to_the_previous_host(session):
    session.apply(result(connect_to_host="core-rtr1"))
    session.apply(result(cli_mode="config-if"))
    assert session.disconnect() == "jump01"
    assert session.hosts["core-rtr1"].cli_mode == "exec"
    assert session.disconnect() is None


def test_snapshot_reflects_state(session):
    session.apply(result(connect_to_host="dc01"))
    snap = session.snapshot()
    assert snap["active_host"] == "dc01"
    assert snap["session_chain"] == ["jump01", "dc01"]
    assert snap["hosts"]["dc01"]["cwd"] == "C:\\Users\\netops"
    assert snap["established_facts"] == session.facts


def test_round_trips_through_json(session):
    session.record("ip a", result("eth0\n"), model="m")
    session.apply(result(connect_to_host="core-rtr1", new_facts=["fact"]))
    session.messages.append({"role": "user", "content": "x"})

    restored = Session.model_validate_json(session.model_dump_json())
    assert restored == session
    assert restored.active.name == "core-rtr1"
