"""Check scenario files before they ship: ``incidently validate [PATH ...]``.

Two kinds of problem are reported:

* **Errors** make a file unusable: YAML that doesn't parse, a schema
  violation, an id that doesn't match the file name, a playthrough that
  can't be resolved, or an id used by two files. The catalog would refuse
  to start with these.
* **Warnings** break house style (see ``docs/writing-scenarios.md``): the
  file loads and plays, but it's a weaker scenario. The bundled scenarios
  must have none; ``--strict`` makes them fail the check too.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from .catalog import BUNDLED_DIR, ScenarioError, instantiate, load_scenario
from .scenario import PLACEHOLDER, Scenario

TOTAL_POINTS = 100
MIN_ROOT_CAUSES = 2
MIN_HINTS = 2
SKILLS_RANGE = (2, 5)
LEAK_WORDS = 5  # a description may not repeat this many words in a row from a secret
SEEDS = 25  # playthroughs resolved per file


@dataclass
class Report:
    path: Path
    scenario: Scenario | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def scenario_files(paths: Iterable[Path]) -> Iterator[Path]:
    """Each path as given, or the ``*.yaml`` files in it if it's a folder."""
    for path in paths:
        if path.is_dir():
            yield from sorted(path.glob("*.yaml"))
        else:
            yield path


def validate(paths: Iterable[Path]) -> list[Report]:
    reports = [_check(path) for path in scenario_files(paths)]
    seen: dict[str, Path] = {}
    for report in reports:
        if report.scenario is None:
            continue
        first = seen.setdefault(report.scenario.id, report.path)
        if first != report.path:
            report.errors.append(f"id {report.scenario.id!r} is also used by {first.name}")
    return reports


def style_warnings(scenario: Scenario) -> list[str]:
    """House-style problems in a valid scenario. Empty for a good one."""
    warnings = []
    if scenario.max_points != TOTAL_POINTS:
        warnings.append(f"grading points total {scenario.max_points}, not {TOTAL_POINTS}")
    variants = scenario.root_cause_variants
    if len(variants) < MIN_ROOT_CAUSES:
        warnings.append(
            f"only {len(variants)} root cause; offer at least {MIN_ROOT_CAUSES} so replays differ"
        )
    if len(set(variants)) != len(variants):
        warnings.append("two root cause variants are identical")
    if len(scenario.hints) < MIN_HINTS:
        warnings.append(f"only {len(scenario.hints)} hint; give at least {MIN_HINTS}")
    if not scenario.description:
        warnings.append("no description for the scenario page")
    elif len(scenario.description) <= len(scenario.summary):
        warnings.append("the description should say more than the summary")
    low, high = SKILLS_RANGE
    if not low <= len(scenario.skills) <= high:
        warnings.append(f"{len(scenario.skills)} skills; list {low} to {high}")
    if leak := _quoted_secret(scenario):
        warnings.append(f"the description gives away hidden text: {leak!r}")
    return warnings


def _check(path: Path) -> Report:
    report = Report(path)
    if path.suffix != ".yaml":
        report.errors.append("not a .yaml file")
        return report
    try:
        scenario = load_scenario(path)
    except ScenarioError as exc:
        # load_scenario prefixes the file name, which the report shows anyway.
        report.errors.append(str(exc).removeprefix(f"{path.name}: "))
        return report
    report.scenario = scenario
    for seed in range(SEEDS):
        try:
            resolved = instantiate(scenario, seed=seed).scenario
        except Exception as exc:  # any failure here is a bug in the file, not the checker
            report.errors.append(f"playthrough with seed {seed} doesn't resolve: {exc}")
            break
        if PLACEHOLDER.search(resolved.model_dump_json()):
            report.errors.append(f"playthrough with seed {seed} still contains a placeholder")
            break
    report.warnings = style_warnings(scenario)
    return report


def _quoted_secret(scenario: Scenario) -> str | None:
    """A run of words from a root cause or the key evidence repeated in the description."""
    text = " ".join(_words(scenario.description))
    for secret in [*scenario.root_cause_variants, scenario.hidden_flag]:
        words = _words(secret)
        for i in range(len(words) - LEAK_WORDS + 1):
            run = " ".join(words[i : i + LEAK_WORDS])
            if f" {run} " in f" {text} ":
                return run
    return None


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def main(
    paths: list[Path],
    *,
    strict: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Print one line per file and return the exit code (0 when everything passes)."""
    out = out or sys.stdout
    err = err or sys.stderr
    reports = validate(paths or [BUNDLED_DIR])
    if not reports:
        print("no scenario files found", file=err)
        return 1
    for report in reports:
        name = report.path.name
        if report.errors:
            for problem in report.errors:
                print(f"error  {name}: {problem}", file=err)
            continue
        s = report.scenario
        print(
            f"ok     {name}: {s.category} / {s.difficulty}, {len(s.hosts)} host(s), "
            f"{len(s.root_cause_variants)} root causes",
            file=out,
        )
        for problem in report.warnings:
            print(f"warn   {name}: {problem}", file=err)

    errors = sum(1 for r in reports if r.errors)
    warned = sum(1 for r in reports if r.warnings and not r.errors)
    summary = f"{len(reports)} file(s): {errors} with errors, {warned} with warnings"
    print(summary, file=out)
    return 1 if errors or (strict and warned) else 0
