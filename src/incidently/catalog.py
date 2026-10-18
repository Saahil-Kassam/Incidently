"""Load scenario blueprints and turn them into playable instances.

Scenarios live as ``<id>.yaml`` files. The bundled ones ship inside the
package (``incidently/scenarios``) so they are available however Incidently
is installed. Every file is validated when the catalog loads, so a broken
scenario stops the app at startup rather than mid-session.

:func:`instantiate` fills in a scenario's ``{{placeholders}}`` and picks one
hidden root cause. Both choices come from a single seed, so any session can
be reproduced exactly from its seed.
"""

from __future__ import annotations

import random
import secrets
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from .scenario import PLACEHOLDER, Scenario

BUNDLED_DIR = Path(str(resources.files("incidently") / "scenarios"))

# "safe" builds only plain dicts, lists, and scalars; YAML tags cannot run code.
_yaml = YAML(typ="safe", pure=True)


class ScenarioError(Exception):
    """A scenario file is missing, malformed, or fails validation."""


@dataclass(frozen=True)
class ScenarioInstance:
    """One randomised playthrough of a scenario.

    ``root_cause`` is the hidden answer. It must never be sent to the player.
    """

    scenario: Scenario
    root_cause: str
    seed: int
    values: Mapping[str, str]


class Catalog:
    """The validated set of scenarios available to play, in file-name order."""

    def __init__(self, scenarios: list[Scenario]) -> None:
        self._by_id = {s.id: s for s in scenarios}

    @classmethod
    def load(cls, directory: Path | None = None) -> Catalog:
        """Load every ``*.yaml`` file in ``directory`` (default: bundled scenarios)."""
        directory = BUNDLED_DIR if directory is None else directory
        if not directory.is_dir():
            raise ScenarioError(f"scenario directory not found: {directory}")
        scenarios = [load_scenario(path) for path in sorted(directory.glob("*.yaml"))]
        if not scenarios:
            raise ScenarioError(f"no scenario files (*.yaml) in {directory}")
        return cls(scenarios)

    def __iter__(self) -> Iterator[Scenario]:
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, scenario_id: object) -> bool:
        return scenario_id in self._by_id

    def get(self, scenario_id: str) -> Scenario:
        try:
            return self._by_id[scenario_id]
        except KeyError:
            raise ScenarioError(f"unknown scenario: {scenario_id!r}") from None


def load_scenario(path: Path) -> Scenario:
    """Parse and validate one scenario file. Errors name the file."""
    try:
        raw = _yaml.load(path.read_text(encoding="utf-8"))
    except (OSError, YAMLError) as exc:
        raise ScenarioError(f"{path.name}: cannot read YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path.name}: top level must be a mapping")
    try:
        scenario = Scenario.model_validate(raw)
    except ValidationError as exc:
        raise ScenarioError(f"{path.name}: {exc}") from exc
    if scenario.id != path.stem:
        raise ScenarioError(f"{path.name}: id {scenario.id!r} must match the file name")
    return scenario


def instantiate(scenario: Scenario, seed: int | None = None) -> ScenarioInstance:
    """Fill in placeholders and pick a hidden root cause.

    The same scenario and seed always give the same result. With no seed, a
    random one is chosen and recorded on the instance.
    """
    if seed is None:
        seed = secrets.randbelow(2**32)
    rng = random.Random(seed)
    values = {key: rng.choice(opts) for key, opts in sorted(scenario.randomize.items())}

    data = _substitute(scenario.model_dump(exclude={"randomize"}), values)
    resolved = Scenario.model_validate(data)
    return ScenarioInstance(
        scenario=resolved,
        root_cause=rng.choice(resolved.root_cause_variants),
        seed=seed,
        values=values,
    )


def _substitute(node: Any, values: Mapping[str, str]) -> Any:
    if isinstance(node, str):
        return PLACEHOLDER.sub(lambda m: values[m.group(1)], node)
    if isinstance(node, list):
        return [_substitute(v, values) for v in node]
    if isinstance(node, dict):
        return {k: _substitute(v, values) for k, v in node.items()}
    return node
