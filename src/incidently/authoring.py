"""Write a new scenario from a theme, such as "BGP flap" or "VLAN mismatch".

The model drafts a :class:`incidently.replies.ScenarioDraft`, which is turned
into a :class:`Scenario` and checked with the same rules as ``incidently
validate``: the schema, playthroughs that resolve, and house style. If
anything fails, the problems go back to the model as a follow-up message and
it revises the draft, up to :data:`DRAFT_ATTEMPTS` times in all. A draft that
still has errors is rejected; house-style warnings that survive the last
revision are accepted and reported, except a description that gives away the
cause, which always counts as an error.

Calls are cached: the system prompt (house rules and an example scenario)
is the same for every generation, and a revision resends the drafts before
it.

The result is in the "Custom" category with an id made from its title, ready
for :meth:`incidently.catalog.Catalog.add_custom`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import ValidationError

from . import prompts
from .catalog import BUNDLED_DIR, CUSTOM_CATEGORY, Catalog
from .config import Settings
from .llm import StructuredLLM, Usage
from .pricing import total_cost
from .replies import ScenarioDraft
from .scenario import Difficulty, Scenario
from .validate import leak_warning, playthrough_errors, style_warnings

log = logging.getLogger(__name__)

DRAFT_ATTEMPTS = 3
DRAFT_MAX_TOKENS = 32_000
MAX_THEME_LENGTH = 200
EXAMPLE_SCENARIO = "dhcp-pool-exhaustion"

Call = tuple[str, Usage]  # the model that served a reply, and its tokens


class AuthoringError(Exception):
    """The model's drafts never became a valid scenario."""


@dataclass(frozen=True)
class Generated:
    scenario: Scenario
    calls: list[Call]
    warnings: list[str] = field(default_factory=list)  # house style left after the last revision

    @property
    def usage(self) -> Usage:
        return sum((usage for _, usage in self.calls), Usage())

    def estimated_cost(self) -> float | None:
        total, complete = total_cost(self.calls)
        return total if complete else None


def generate(
    llm: StructuredLLM,
    settings: Settings,
    catalog: Catalog,
    *,
    theme: str,
    difficulty: Difficulty,
    calls: list[Call] | None = None,
) -> Generated:
    """Draft, check, and revise a scenario about ``theme``.

    Each reply's model and tokens are appended to ``calls`` as they arrive,
    so a caller passing its own list can account for the tokens even when
    generation fails part-way. Raises :class:`AuthoringError` if no draft
    became valid, and :class:`incidently.llm.LLMError` if a model call fails.
    """
    theme = " ".join(theme.split())
    if not theme:
        raise AuthoringError("describe the incident you want, for example 'BGP flap'")
    if len(theme) > MAX_THEME_LENGTH:
        raise AuthoringError(f"keep the theme under {MAX_THEME_LENGTH} characters")
    calls = [] if calls is None else calls

    messages = [
        {
            "role": "user",
            "content": prompts.authoring_message(
                theme, difficulty, [s.title for s in catalog], _vocabulary(catalog)
            ),
        }
    ]
    problems: list[str] = []
    for attempt in range(1, DRAFT_ATTEMPTS + 1):
        result = llm.generate(
            system=prompts.authoring_system(_example()),
            messages=messages,
            schema=ScenarioDraft,
            effort=settings.deep_effort,
            max_tokens=DRAFT_MAX_TOKENS,
            cache=True,
        )
        calls.append((result.model, result.usage))
        draft = result.output
        scenario, problems, warnings = check(draft, catalog.new_id(draft.title), difficulty)
        if scenario is not None and not problems and (not warnings or attempt == DRAFT_ATTEMPTS):
            if warnings:
                log.info("generated %s with warnings: %s", scenario.id, "; ".join(warnings))
            return Generated(scenario, list(calls), warnings)
        problems += warnings
        log.info("draft %d of %r needs revising: %s", attempt, theme, "; ".join(problems))
        messages += [
            {"role": "assistant", "content": draft.model_dump_json()},
            {"role": "user", "content": prompts.revision_message(problems)},
        ]
    raise AuthoringError(
        f"the model couldn't write a valid incident in {DRAFT_ATTEMPTS} tries "
        f"({'; '.join(problems)})"
    )


def check(
    draft: ScenarioDraft, scenario_id: str, difficulty: Difficulty
) -> tuple[Scenario | None, list[str], list[str]]:
    """The draft as a scenario (None if invalid), its errors, and its style warnings."""
    names = [p.name for p in draft.placeholders]
    if dupes := sorted({n for n in names if names.count(n) > 1}):
        return None, [f"placeholder names must be unique; duplicated: {dupes}"], []
    data = {
        "id": scenario_id,
        "title": draft.title,
        "category": CUSTOM_CATEGORY,
        "topic": draft.topic,
        "difficulty": difficulty,
        "summary": draft.summary,
        "description": draft.description,
        "skills": draft.skills,
        "randomize": {p.name: p.values for p in draft.placeholders},
        "objective": draft.objective,
        "hosts": [h.model_dump() for h in draft.hosts],
        "start_host": draft.start_host,
        "root_cause_variants": draft.root_cause_variants,
        "hidden_flag": draft.hidden_flag,
        "resolution_condition": draft.resolution_condition,
        "grading": [c.model_dump() for c in draft.grading],
        "hints": draft.hints,
        "simulation_notes": draft.simulation_notes,
    }
    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as exc:
        return None, [_describe(error) for error in exc.errors()], []

    errors = playthrough_errors(scenario)
    leak = leak_warning(scenario)
    if leak:
        errors.append(leak)
    warnings = [w for w in style_warnings(scenario) if w != leak]
    return scenario, errors, warnings


def _describe(error: dict) -> str:
    where = ".".join(str(part) for part in error["loc"])
    message = str(error["msg"]).removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


def _vocabulary(catalog: Catalog) -> list[str]:
    seen: dict[str, None] = {}
    for scenario in catalog:
        for skill in scenario.skills:
            seen.setdefault(skill, None)
    return list(seen)


def _example() -> str:
    return (BUNDLED_DIR / f"{EXAMPLE_SCENARIO}.yaml").read_text(encoding="utf-8")
