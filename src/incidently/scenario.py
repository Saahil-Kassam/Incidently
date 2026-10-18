"""Scenario blueprint schema.

A scenario is an enterprise incident described in YAML: the hosts the player
can log in to, a set of possible hidden root causes, and the criteria used to
grade the player's work. Text fields may contain ``{{placeholder}}`` markers
that are filled in from the ``randomize`` table each time the scenario is
played (see :mod:`incidently.catalog`).

The models reject unknown keys so that a typo in a scenario file fails loudly
instead of being silently ignored.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Shell = Literal["bash", "powershell", "cisco-ios"]
Difficulty = Literal["beginner", "intermediate", "advanced"]

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
SLUG = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
SNAKE = r"^[a-z][a-z0-9_]*$"


class _Strict(BaseModel):
    # YAML folded blocks (>) leave a trailing newline; strip it on load.
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Host(_Strict):
    name: str = Field(min_length=1, description="Short hostname, e.g. dhcp01 or core-rtr1")
    os: str = Field(min_length=1, description="Operating system and version")
    shell: Shell
    role: str = Field(min_length=1, description="What the host does in the environment")
    ip: str = Field(min_length=1)
    user: str = Field(min_length=1, description="Account the player is logged in as")
    cwd: str = Field(description="Starting working directory ('' for network devices)")


class Criterion(_Strict):
    id: str = Field(pattern=SNAKE, description="Unique within the scenario")
    description: str = Field(min_length=1)
    points: int = Field(gt=0)
    required: bool = Field(description="Must be met for the incident to count as resolved")


class Scenario(_Strict):
    id: str = Field(pattern=SLUG, description="Stable identifier; also the file name")
    title: str = Field(min_length=1)
    category: str = Field(min_length=1, description="Hub grouping, e.g. 'Networking'")
    topic: str = Field(min_length=1, description="Subject within the category, e.g. 'DHCP'")
    difficulty: Difficulty
    summary: str = Field(min_length=1, description="One-line teaser shown in the hub")
    randomize: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Placeholder name -> possible values; one is picked per session",
    )
    objective: str = Field(min_length=1)
    hosts: list[Host] = Field(min_length=1)
    start_host: str
    root_cause_variants: list[str] = Field(
        min_length=1, description="Possible hidden causes; one is picked per session"
    )
    hidden_flag: str = Field(min_length=1, description="The evidence that proves the cause")
    resolution_condition: str = Field(min_length=1)
    grading: list[Criterion] = Field(min_length=1)
    hints: list[str] = Field(min_length=1, description="Ordered from vague to specific")
    simulation_notes: str = Field(min_length=1, description="Realism guidance for the simulator")

    @field_validator("randomize", mode="before")
    @classmethod
    def _stringify_choices(cls, value: Any) -> Any:
        # YAML reads bare numbers such as 10.44 as floats; placeholders are text.
        if isinstance(value, dict):
            return {
                key: [str(v) for v in opts] if isinstance(opts, list) else opts
                for key, opts in value.items()
            }
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Scenario:
        names = [h.name for h in self.hosts]
        _require_unique(names, "host names")
        if self.start_host not in names:
            raise ValueError(f"start_host {self.start_host!r} is not one of {names}")

        _require_unique([c.id for c in self.grading], "grading criterion ids")
        if not any(c.required for c in self.grading):
            raise ValueError("at least one grading criterion must be required")

        for key, opts in self.randomize.items():
            if not PLACEHOLDER.fullmatch("{{" + key + "}}"):
                raise ValueError(f"randomize key {key!r} is not a valid placeholder name")
            if not opts:
                raise ValueError(f"randomize.{key} must list at least one value")
            _require_unique(opts, f"randomize.{key} values")

        used = set(self.placeholders())
        declared = set(self.randomize)
        if undeclared := sorted(used - declared):
            raise ValueError(f"placeholders used but not declared in randomize: {undeclared}")
        if unused := sorted(declared - used):
            raise ValueError(f"randomize keys never used in the scenario: {unused}")
        return self

    @property
    def max_points(self) -> int:
        return sum(c.points for c in self.grading)

    def host(self, name: str) -> Host | None:
        """Look up a host by name, ignoring case."""
        return next((h for h in self.hosts if h.name.lower() == name.lower()), None)

    def placeholders(self) -> Iterator[str]:
        """Yield every placeholder name used in the scenario's text fields."""
        for text in _strings(self.model_dump(exclude={"randomize"})):
            for match in PLACEHOLDER.finditer(text):
                yield match.group(1)


def _require_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    dupes = sorted({v for v in values if v in seen or seen.add(v)})
    if dupes:
        raise ValueError(f"{label} must be unique; duplicated: {dupes}")


def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
