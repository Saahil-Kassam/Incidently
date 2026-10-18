"""The state of one playthrough.

The Messages API is stateless, so :class:`Session` is the source of truth for
the simulated environment: which host the player is on (and the chain of
remote sessions that got them there), each host's user, working directory and
Cisco mode, service changes, the facts the model has committed to, and the
command history. A compact snapshot goes with every command so the model
never loses track of where the player is.

Sessions are Pydantic models, so they round-trip through JSON unchanged; a
later milestone stores them in SQLite.
"""

from __future__ import annotations

import posixpath
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from .catalog import ScenarioInstance
from .replies import Bootstrap, CommandResult, Ticket
from .scenario import Host, Scenario, Shell

# Output kept per command. Enough for grading; stops one huge listing from
# bloating every later prompt and the saved session.
MAX_RECORDED_OUTPUT = 4000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class HostState(BaseModel):
    name: str
    os: str
    shell: Shell
    role: str
    ip: str
    user: str
    cwd: str
    cli_mode: str = ""  # Cisco IOS only: exec, config, config-if, ...

    @classmethod
    def from_host(cls, host: Host) -> HostState:
        return cls(
            **host.model_dump(),
            cli_mode="exec" if host.shell == "cisco-ios" else "",
        )


class CommandRecord(BaseModel):
    turn: int
    host: str
    user: str
    cwd: str
    command: str
    local: bool = False  # handled by the engine without a model call
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    model: str | None = None  # the model that simulated it
    at: str = Field(default_factory=_now)


class Session(BaseModel):
    scenario: Scenario
    root_cause: str
    seed: int
    ticket: Ticket
    facts: list[str] = Field(default_factory=list)
    hosts: dict[str, HostState]
    chain: list[str] = Field(description="Hosts the player is nested into; the last is active")
    services: dict[str, str] = Field(default_factory=dict)  # "host:service" -> status
    history: list[CommandRecord] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)  # model conversation
    started_at: str = Field(default_factory=_now)

    @classmethod
    def create(cls, instance: ScenarioInstance, bootstrap: Bootstrap) -> Session:
        scenario = instance.scenario
        session = cls(
            scenario=scenario,
            root_cause=instance.root_cause,
            seed=instance.seed,
            ticket=bootstrap.ticket,
            hosts={h.name: HostState.from_host(h) for h in scenario.hosts},
            chain=[scenario.start_host],
        )
        session._add_facts(bootstrap.facts)
        return session

    # --- reading ------------------------------------------------------------

    @property
    def active(self) -> HostState:
        return self.hosts[self.chain[-1]]

    @property
    def remote(self) -> bool:
        """Whether the player is inside a remote session opened from another host."""
        return len(self.chain) > 1

    @property
    def turn(self) -> int:
        return len(self.history)

    def snapshot(self) -> dict[str, Any]:
        """Compact state sent with every command. Deterministic for a given state."""
        return {
            "turn": self.turn,
            "active_host": self.active.name,
            "session_chain": self.chain,
            "hosts": {
                name: {"user": h.user, "cwd": h.cwd, "cli_mode": h.cli_mode}
                for name, h in self.hosts.items()
            },
            "service_changes": self.services,
            "established_facts": self.facts,
        }

    # --- changing -----------------------------------------------------------

    def record(
        self,
        command: str,
        result: CommandResult | None = None,
        *,
        model: str | None = None,
    ) -> CommandRecord:
        """Add a command to the history, as it was typed on the active host."""
        host = self.active
        entry = CommandRecord(
            turn=self.turn + 1,
            host=host.name,
            user=host.user,
            cwd=host.cwd,
            command=command,
            local=result is None,
        )
        if result is not None:
            entry.stdout = result.stdout[:MAX_RECORDED_OUTPUT]
            entry.stderr = result.stderr[:MAX_RECORDED_OUTPUT]
            entry.exit_code = result.exit_code
            entry.model = model
        self.history.append(entry)
        return entry

    def apply(self, result: CommandResult) -> None:
        """Fold a simulated command's side effects into the state.

        Values the model invents are checked against the inventory, so a reply
        can't move the player to a host that doesn't exist.
        """
        host = self.active
        updates = result.state_updates

        if user := updates.user.strip():
            host.user = user
        if cwd := updates.cwd.strip():
            host.cwd = _resolve_cwd(host, cwd)
        if (mode := updates.cli_mode.strip()) and host.shell == "cisco-ios":
            host.cli_mode = mode
        for change in updates.services:
            target = self.scenario.host(change.host) or self.scenario.host(host.name)
            if target is not None and change.name.strip():
                self.services[f"{target.name}:{change.name.strip()}"] = change.status.strip()
        self._add_facts(updates.new_facts)

        if target_name := updates.connect_to_host.strip():
            target = self.scenario.host(target_name)
            if target is not None and target.name != host.name:
                self.chain.append(target.name)

    def disconnect(self) -> str | None:
        """Leave the active remote session; return the host returned to, or None if at the top."""
        if not self.remote:
            return None
        left = self.hosts[self.chain.pop()]
        if left.shell == "cisco-ios":
            left.cli_mode = "exec"
        return self.active.name

    def _add_facts(self, facts: list[str]) -> None:
        for fact in facts:
            fact = fact.strip()
            if fact and fact not in self.facts:
                self.facts.append(fact)


def _resolve_cwd(host: HostState, cwd: str) -> str:
    """Make a relative bash path absolute; other shells report full paths already."""
    if host.shell == "bash" and not cwd.startswith(("/", "~")):
        return posixpath.normpath(posixpath.join(host.cwd or "/", cwd))
    return cwd
