"""Load scenario blueprints and turn them into playable instances.

Scenarios live as ``<id>.yaml`` files. The bundled ones ship inside the
package (``incidently/scenarios``) so they are available however Incidently
is installed. Every file is validated when the catalog loads, so a broken
scenario stops the app at startup rather than mid-session.

Custom scenarios (generated from a theme, see :mod:`incidently.authoring`)
live in a folder in the data directory instead. They are the player's own
files, so one that has become invalid is skipped with a warning rather than
stopping the app, and they can be added and deleted while the app runs.

:func:`instantiate` fills in a scenario's ``{{placeholders}}`` and picks one
hidden root cause. Both choices come from a single seed, so any session can
be reproduced exactly from its seed.
"""

from __future__ import annotations

import logging
import os
import random
import re
import secrets
import tempfile
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import LiteralScalarString

from .scenario import PLACEHOLDER, Scenario

log = logging.getLogger(__name__)

BUNDLED_DIR = Path(str(resources.files("incidently") / "scenarios"))

# "safe" builds only plain dicts, lists, and scalars; YAML tags cannot run code.
_yaml = YAML(typ="safe", pure=True)

CUSTOM_CATEGORY = "Custom"
MAX_ID_LENGTH = 60


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
    """The validated set of scenarios available to play.

    Bundled scenarios come first, in file-name order, then custom ones in the
    order they were added. Safe to share between threads.
    """

    def __init__(self, scenarios: list[Scenario], custom_dir: Path | None = None) -> None:
        self._by_id = {s.id: s for s in scenarios}
        self._custom: set[str] = set()
        self._custom_dir = custom_dir
        self._lock = threading.Lock()

    @classmethod
    def load(cls, directory: Path | None = None, custom_dir: Path | None = None) -> Catalog:
        """Load every ``*.yaml`` file in ``directory`` (default: bundled scenarios).

        ``custom_dir``, if given, is where custom scenarios are read from and
        saved to; it need not exist yet.
        """
        directory = BUNDLED_DIR if directory is None else directory
        if not directory.is_dir():
            raise ScenarioError(f"scenario directory not found: {directory}")
        scenarios = [load_scenario(path) for path in sorted(directory.glob("*.yaml"))]
        if not scenarios:
            raise ScenarioError(f"no scenario files (*.yaml) in {directory}")
        catalog = cls(scenarios, custom_dir)
        if custom_dir is not None and custom_dir.is_dir():
            catalog._load_custom(custom_dir)
        return catalog

    def _load_custom(self, directory: Path) -> None:
        # Oldest first, so new ones join the end of the hub as they would have when added.
        for path in sorted(directory.glob("*.yaml"), key=lambda p: (p.stat().st_mtime, p.name)):
            try:
                scenario = load_scenario(path)
            except ScenarioError as exc:
                log.warning("skipping custom scenario: %s", exc)
                continue
            if scenario.id in self._by_id:
                log.warning("skipping custom scenario %s: id already in use", path.name)
                continue
            self._by_id[scenario.id] = scenario
            self._custom.add(scenario.id)

    def __iter__(self) -> Iterator[Scenario]:
        with self._lock:
            return iter(list(self._by_id.values()))

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, scenario_id: object) -> bool:
        return scenario_id in self._by_id

    def get(self, scenario_id: str) -> Scenario:
        try:
            return self._by_id[scenario_id]
        except KeyError:
            raise ScenarioError(f"unknown scenario: {scenario_id!r}") from None

    def is_custom(self, scenario_id: str) -> bool:
        return scenario_id in self._custom

    def new_id(self, title: str) -> str:
        """An id made from ``title`` that no scenario uses yet."""
        base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:MAX_ID_LENGTH].strip("-")
        base = base or "custom-incident"
        with self._lock:
            candidate, n = base, 2
            while candidate in self._by_id:
                candidate, n = f"{base}-{n}", n + 1
            return candidate

    def add_custom(self, scenario: Scenario) -> Path:
        """Save a new custom scenario as ``<id>.yaml`` and make it playable."""
        directory = self._require_custom_dir()
        with self._lock:
            if scenario.id in self._by_id:
                raise ScenarioError(f"scenario id {scenario.id!r} is already in use")
            path = directory / f"{scenario.id}.yaml"
            _write_yaml(path, scenario)
            self._by_id[scenario.id] = scenario
            self._custom.add(scenario.id)
        return path

    def remove_custom(self, scenario_id: str) -> None:
        """Delete a custom scenario's file. Bundled scenarios can't be removed."""
        directory = self._require_custom_dir()
        with self._lock:
            if scenario_id not in self._custom:
                raise ScenarioError(f"{scenario_id!r} is not a custom scenario")
            try:
                (directory / f"{scenario_id}.yaml").unlink(missing_ok=True)
            except OSError as exc:
                raise ScenarioError(f"couldn't delete {scenario_id}.yaml: {exc}") from exc
            del self._by_id[scenario_id]
            self._custom.discard(scenario_id)

    def _require_custom_dir(self) -> Path:
        if self._custom_dir is None:
            raise ScenarioError("this catalog has no folder for custom scenarios")
        return self._custom_dir


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


def _write_yaml(path: Path, scenario: Scenario) -> None:
    """Write the scenario in the bundled files' field order, replacing the file atomically."""
    data = _block_text(scenario.model_dump(mode="json"))
    if not data["randomize"]:
        del data["randomize"]
    out = YAML(pure=True)  # round-trip mode keeps the field order
    out.width = 100
    out.allow_unicode = True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".yaml.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                out.dump(data, f)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise ScenarioError(f"couldn't save {path.name}: {exc}") from exc


def _block_text(node: Any) -> Any:
    """Mark multi-line strings to be written as ``|`` blocks, so the file stays readable."""
    if isinstance(node, str):
        return LiteralScalarString(node) if "\n" in node else node
    if isinstance(node, list):
        return [_block_text(v) for v in node]
    if isinstance(node, dict):
        return {k: _block_text(v) for k, v in node.items()}
    return node


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
