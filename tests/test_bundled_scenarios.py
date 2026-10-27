"""Content checks for the scenarios that ship with Incidently.

The schema enforces structure; these tests enforce house style that every
bundled scenario should follow.
"""

import pytest

from incidently.catalog import Catalog, instantiate
from incidently.scenario import PLACEHOLDER

CATALOG = Catalog.load()
SCENARIOS = list(CATALOG)


def test_skills_are_shared_between_scenarios():
    # A shared vocabulary is what lets progress be grouped by skill.
    counts = {}
    for scenario in SCENARIOS:
        for skill in scenario.skills:
            counts[skill] = counts.get(skill, 0) + 1
    assert sum(1 for n in counts.values() if n > 1) >= 3


def test_bundled_scenarios_by_category():
    by_category = {}
    for scenario in SCENARIOS:
        by_category.setdefault(scenario.category, set()).add(scenario.id)
    assert by_category == {
        "Networking": {
            "dhcp-pool-exhaustion",
            "dns-cache-poisoning",
            "ospf-routing-loop",
            "wifi-8021x-radius",
        },
        "Enterprise Systems": {
            "ad-sync-failure",
            "ios-mdm-enrollment",
            "selinux-web-blocked",
            "sqlite-corruption",
        },
    }

@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
class TestBundledScenario:
    def test_points_total_100(self, scenario):
        assert scenario.max_points == 100

    def test_offers_more_than_one_root_cause(self, scenario):
        assert len(scenario.root_cause_variants) >= 2

    def test_root_causes_are_distinct(self, scenario):
        assert len(set(scenario.root_cause_variants)) == len(scenario.root_cause_variants)

    def test_has_a_description_and_skills(self, scenario):
        assert len(scenario.description) > len(scenario.summary)
        assert 2 <= len(scenario.skills) <= 5

    def test_description_gives_nothing_away(self, scenario):
        text = scenario.description.lower()
        for secret in [*scenario.root_cause_variants, scenario.hidden_flag]:
            # No run of five words from a root cause or the key evidence.
            words = secret.lower().split()
            for i in range(len(words) - 4):
                assert " ".join(words[i : i + 5]) not in text

    def test_resolves_cleanly_across_seeds(self, scenario):
        for seed in range(25):
            resolved = instantiate(scenario, seed=seed).scenario
            assert not PLACEHOLDER.search(resolved.model_dump_json())
