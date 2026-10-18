import pytest

from incidently.session import HostState
from incidently.shell import MAX_COMMAND_LENGTH, Kind, classify, prompt


def host(shell="bash", **overrides):
    fields = {
        "name": "jump01",
        "os": "Ubuntu",
        "shell": shell,
        "role": "Jump host",
        "ip": "10.0.0.10",
        "user": "netops",
        "cwd": "/home/netops",
        "cli_mode": "exec" if shell == "cisco-ios" else "",
    }
    return HostState(**(fields | overrides))


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("", Kind.EMPTY),
        ("   ", Kind.EMPTY),
        ("clear", Kind.CLEAR),
        ("CLS", Kind.CLEAR),
        ("history", Kind.HISTORY),
        ("exit", Kind.EXIT),
        ("logout", Kind.EXIT),
        ("Exit-PSSession", Kind.EXIT),
        ("ip route show", Kind.SIMULATE),
        ("exit 1", Kind.SIMULATE),  # not the bare built-in
        ("top -b -n 1", Kind.SIMULATE),
        ("tail -n 50 /var/log/syslog", Kind.SIMULATE),
        ("cat /etc/vimrc", Kind.SIMULATE),
    ],
)
def test_classify_bash(line, kind):
    assert classify(line, host()).kind is kind


@pytest.mark.parametrize(
    ("line", "program"),
    [
        ("vim /etc/hosts", "vim"),
        ("sudo nano /etc/hosts", "nano"),
        ("sudo -E vi x", "vi"),
        ("EDITOR=vim less /var/log/syslog", "less"),
        ("/usr/bin/vim x", "vim"),
        ("top", "top"),
        ("tail -f /var/log/syslog", "tail"),
        ("journalctl -u unbound --follow", "journalctl"),
    ],
)
def test_interactive_programs_are_blocked(line, program):
    c = classify(line, host())
    assert c.kind is Kind.BLOCKED
    assert program in c.message


def test_continuous_ping_is_blocked_on_powershell_only():
    assert classify("ping -t 10.0.0.1", host("powershell")).kind is Kind.BLOCKED
    assert classify("ping -t 64 10.0.0.1", host("bash")).kind is Kind.SIMULATE  # -t is TTL


def test_unbalanced_quotes_go_to_the_model():
    assert classify("echo 'oops", host()).kind is Kind.SIMULATE


def test_oversized_input_is_blocked():
    c = classify("x" * (MAX_COMMAND_LENGTH + 1), host())
    assert c.kind is Kind.BLOCKED
    assert "longer than" in c.message


def test_cisco_commands_are_never_blocked_as_interactive():
    assert classify("more system:running-config", host("cisco-ios")).kind is Kind.SIMULATE


@pytest.mark.parametrize(
    ("mode", "line", "expected"),
    [
        ("config-if", "exit", "config"),
        ("config-router", "exit", "config"),
        ("config", "exit", "exec"),
        ("config-if", "end", "exec"),
        ("config", "END", "exec"),
    ],
)
def test_cisco_config_mode_exit_steps_back(mode, line, expected):
    c = classify(line, host("cisco-ios", cli_mode=mode))
    assert c.kind is Kind.MODE
    assert c.mode == expected


def test_cisco_exit_in_exec_mode_disconnects():
    assert classify("exit", host("cisco-ios")).kind is Kind.EXIT


@pytest.mark.parametrize(
    ("state", "remote", "expected"),
    [
        ({}, False, "netops@jump01:~$ "),
        ({"cwd": "/home/netops/logs"}, False, "netops@jump01:~/logs$ "),
        ({"cwd": "/var/log"}, False, "netops@jump01:/var/log$ "),
        ({"user": "root", "cwd": "/root"}, False, "root@jump01:~# "),
        ({"cwd": "/home/netopsx"}, False, "netops@jump01:/home/netopsx$ "),
    ],
)
def test_bash_prompt(state, remote, expected):
    assert prompt(host(**state), remote=remote) == expected


def test_powershell_prompt():
    ps = host("powershell", name="dc01", cwd="C:\\Users\\netops")
    assert prompt(ps) == "PS C:\\Users\\netops> "
    assert prompt(ps, remote=True) == "[dc01]: PS C:\\Users\\netops> "


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("exec", "core-rtr1#"),
        ("config", "core-rtr1(config)#"),
        ("config-if", "core-rtr1(config-if)#"),
    ],
)
def test_cisco_prompt(mode, expected):
    assert prompt(host("cisco-ios", name="core-rtr1", cwd="", cli_mode=mode)) == expected
