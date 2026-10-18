"""Content checks for the scenarios that ship with Incidently.

The schema enforces structure; these tests enforce house style that every
bundled scenario should follow.
"""

import pytest

from incidently.catalog import Catalog, instantiate
from incidently.scenario import PLACEHOLDER

CATALOG = Catalog.load()
SCENARIOS = list(CATALOG)


def test_networking_scenarios_are_bundled():
    assert {s.id for s in SCENARIOS} == {
        "dhcp-pool-exhaustion",
        "dns-cache-poisoning",
        "ospf-routing-loop",
        "wifi-8021x-radius",
    }


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
class TestBundledScenario:
    def test_points_total_100(self, scenario):
        assert scenario.max_points == 100

    def test_offers_more_than_one_root_cause(self, scenario):
        assert len(scenario.root_cause_variants) >= 2

    def test_root_causes_are_distinct(self, scenario):
        assert len(set(scenario.root_cause_variants)) == len(scenario.root_cause_variants)

    def test_resolves_cleanly_across_seeds(self, scenario):
        for seed in range(25):
            resolved = instantiate(scenario, seed=seed).scenario
            assert not PLACEHOLDER.search(resolved.model_dump_json())
