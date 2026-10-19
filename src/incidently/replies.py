"""The shapes the model is asked to reply with during play.

Each class here is sent to the API as a structured-output JSON schema, so the
model's reply must match it exactly. Two rules keep that working:

* Every field is required and has no default. Structured outputs need every
  property declared and required; "nothing changed" is an empty string or an
  empty list, not a missing key.
* No free-form dicts. Anything keyed is a list of small objects instead.

Field descriptions are part of the schema the model reads, so they are
written as instructions to it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ticket(_Reply):
    id: str = Field(description="Ticket number, e.g. INC0048213")
    priority: Literal["P1", "P2", "P3", "P4"]
    reported_by: str = Field(description="Name and team, or the monitoring system")
    summary: str = Field(description="One-line ticket title")
    description: str = Field(
        description="What the reporter observed, in their own words. Symptoms only: never the "
        "root cause or the fix."
    )


class Bootstrap(_Reply):
    """First call of a session: turn the blueprint into concrete, consistent details."""

    ticket: Ticket
    facts: list[str] = Field(
        description="8-15 concrete environment details consistent with the hidden root cause "
        "(IPs, versions, timestamps, error codes, log lines, affected users). Hidden from the "
        "player; the simulation must honour them."
    )


class ServiceChange(_Reply):
    host: str = Field(description="Inventory host name")
    name: str = Field(description="Service, daemon, process, or interface name")
    status: str = Field(description="New state, e.g. running, stopped, failed, up, down")


class StateUpdates(_Reply):
    cwd: str = Field(description="New working directory, or '' if unchanged")
    user: str = Field(description="New effective user (su, runas), or '' if unchanged")
    connect_to_host: str = Field(
        description="Inventory host a remote session was just opened to (ssh, telnet, "
        "Enter-PSSession), or '' if none"
    )
    cli_mode: str = Field(
        description="Cisco IOS mode after this command (exec, config, config-if, "
        "config-router, ...), or '' if unchanged or not a Cisco device"
    )
    services: list[ServiceChange] = Field(
        description="Every service or interface whose state this command changed"
    )
    new_facts: list[str] = Field(
        description="New concrete details this output invented that later output must honour. "
        "One short line each; don't repeat established facts."
    )


class CommandResult(_Reply):
    """One simulated command.

    ``stdout`` stays the first field so a later milestone can stream it to the
    terminal while the rest of the reply is still being generated.
    """

    stdout: str
    stderr: str
    exit_code: int = Field(ge=0, le=255)
    state_updates: StateUpdates


class HintReply(_Reply):
    """A hint written for where the player actually is."""

    hint: str = Field(description="1-3 sentences addressed to the player as 'you', plain text")


class CriterionAssessment(_Reply):
    id: str = Field(description="The grading criterion's id")
    met: bool
    turn: int = Field(description="Number (#) of the command that first met it, or 0 if unmet")
    feedback: str = Field(description="One specific sentence to the player about this criterion")


class RiskEvent(_Reply):
    turn: int = Field(description="Number (#) of the risky command")
    level: Literal["high", "destructive"]
    reason: str = Field(description="One sentence on why it was risky")


class Assessment(_Reply):
    """The grading call's verdict on the whole command log, plus the debrief text."""

    criteria: list[CriterionAssessment] = Field(
        description="One entry per grading criterion, in the order given"
    )
    key_evidence_turn: int = Field(
        description="Number (#) of the command whose output first revealed the key evidence, or 0"
    )
    resolved: bool = Field(description="Whether the resolution condition held at the end")
    resolved_turn: int = Field(description="Number (#) of the command that completed the fix, or 0")
    risk_events: list[RiskEvent]
    summary: str = Field(description="2-4 candid sentences on how the incident went")
    root_cause_explanation: str = Field(
        description="What the root cause was and how its evidence surfaced in this session"
    )
    strengths: list[str] = Field(description="Specific things done well, citing commands")
    improvements: list[str] = Field(description="Specific things to do better, citing commands")
    ideal_path: list[str] = Field(
        description="The commands an expert would have run on these hosts, in order"
    )
