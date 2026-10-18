"""Terminal behaviour the engine handles itself, without a model call.

:func:`classify` sorts each line the player types:

* shell built-ins the simulator owns (``clear``, ``history``, ``exit``),
* Cisco ``end``/``exit`` inside configuration mode, which step back a mode,
* programs a line-based terminal can't host (editors, pagers, ``top``,
  ``tail -f``...), which are refused with a non-interactive alternative,
* everything else, which goes to the model.

:func:`prompt` renders the shell prompt for a host, e.g.
``netops@noc-jump01:~$`` or ``core-rtr1(config-if)#``.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum

from .session import HostState

MAX_COMMAND_LENGTH = 1000


class Kind(Enum):
    EMPTY = "empty"
    CLEAR = "clear"
    HISTORY = "history"
    EXIT = "exit"
    MODE = "mode"  # Cisco: leave a configuration mode
    BLOCKED = "blocked"
    SIMULATE = "simulate"


@dataclass(frozen=True)
class Classified:
    kind: Kind
    command: str
    message: str = ""  # why a BLOCKED command was refused
    mode: str = ""  # the Cisco mode a MODE command returns to


EXIT_WORDS = frozenset({"exit", "logout", "logoff", "quit", "disconnect", "exit-pssession"})
CLEAR_WORDS = frozenset({"clear", "cls", "clear-host"})
HISTORY_WORDS = frozenset({"history", "get-history", "h", "show history"})

_EDIT_LINUX = "use sed -i, tee, or echo >> to change files"
_EDIT_WINDOWS = "use Set-Content or Add-Content to change files"
_PAGER = "use cat, head, tail, or grep"

# Full-screen or never-ending programs, with what to use instead.
INTERACTIVE = {
    "vi": _EDIT_LINUX,
    "vim": _EDIT_LINUX,
    "nvim": _EDIT_LINUX,
    "nano": _EDIT_LINUX,
    "emacs": _EDIT_LINUX,
    "edit": _EDIT_WINDOWS,
    "notepad": _EDIT_WINDOWS,
    "notepad.exe": _EDIT_WINDOWS,
    "less": _PAGER,
    "more": _PAGER,
    "htop": "use `top -b -n 1` or `ps aux`",
    "watch": "run the command once instead",
    "mmc": "use the equivalent PowerShell cmdlet",
    "mmc.exe": "use the equivalent PowerShell cmdlet",
    "regedit": "use Get-ItemProperty or Set-ItemProperty",
}

_WRAPPERS = frozenset({"sudo", "doas", "nohup", "time", "env"})
_FOLLOW_FLAG = re.compile(r"(^|\s)(-f|-F|--follow)(\s|$)")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def classify(line: str, host: HostState) -> Classified:
    command = line.strip()
    if not command:
        return Classified(Kind.EMPTY, command)
    if len(command) > MAX_COMMAND_LENGTH:
        return Classified(
            Kind.BLOCKED, command, message=f"input is longer than {MAX_COMMAND_LENGTH} characters"
        )

    lowered = " ".join(command.lower().split())
    if host.shell == "cisco-ios" and host.cli_mode not in ("", "exec"):
        if lowered == "end" or (lowered == "exit" and host.cli_mode == "config"):
            return Classified(Kind.MODE, command, mode="exec")
        if lowered == "exit":
            return Classified(Kind.MODE, command, mode="config")

    if lowered in EXIT_WORDS:
        return Classified(Kind.EXIT, command)
    if lowered in CLEAR_WORDS:
        return Classified(Kind.CLEAR, command)
    if lowered in HISTORY_WORDS:
        return Classified(Kind.HISTORY, command)

    if host.shell != "cisco-ios":
        reason = _interactive_reason(command, host)
        if reason:
            return Classified(Kind.BLOCKED, command, message=reason)
    return Classified(Kind.SIMULATE, command)


def prompt(host: HostState, *, remote: bool = False) -> str:
    """The prompt shown before the player's next command on ``host``."""
    if host.shell == "cisco-ios":
        mode = "" if host.cli_mode in ("", "exec") else f"({host.cli_mode})"
        return f"{host.name}{mode}#"
    if host.shell == "powershell":
        prefix = f"[{host.name}]: " if remote else ""
        return f"{prefix}PS {host.cwd}> "
    home = "/root" if host.user == "root" else f"/home/{host.user}"
    cwd = host.cwd
    if cwd == home or cwd.startswith(home + "/"):
        cwd = "~" + cwd[len(home) :]
    return f"{host.user}@{host.name}:{cwd}{'#' if host.user == 'root' else '$'} "


def _interactive_reason(command: str, host: HostState) -> str:
    program, args = _program(command)
    if program in INTERACTIVE:
        return f"'{program}' is interactive and not supported here; {INTERACTIVE[program]}"
    if program == "top" and "-b" not in args:
        return "interactive 'top' is not supported here; use `top -b -n 1`"
    if program in ("tail", "journalctl") and _FOLLOW_FLAG.search(" ".join(args)):
        return f"'{program}' in follow mode never returns; drop -f and use -n instead"
    if program in ("ping", "ping.exe") and host.shell == "powershell" and "-t" in args:
        return "continuous ping never returns; use `ping -n 4`"
    return ""


def _program(command: str) -> tuple[str, list[str]]:
    """The program a command runs (past sudo, env assignments...) and its arguments."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:  # unbalanced quotes: the model will report the real error
        tokens = command.split()
    for index, token in enumerate(tokens):
        # Everything before the program: wrappers, their flags, VAR=value.
        if token in _WRAPPERS or token.startswith("-") or _ASSIGNMENT.match(token):
            continue
        return token.lower().rsplit("/", 1)[-1], tokens[index + 1 :]
    return "", []
