"""Content checks for the scenarios that ship with Incidently.

The schema enforces structure; house style (see docs/writing-scenarios.md)
is checked by the same rules ``incidently validate`` uses, so the bundled
scenarios are held to exactly what authors are told.
"""

import pytest

from incidently.catalog import BUNDLED_DIR, Catalog
from incidently.validate import validate

CATALOG = Catalog.load()
SCENARIOS = list(CATALOG)
REPORTS = validate([BUNDLED_DIR])


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


@pytest.mark.parametrize("report", REPORTS, ids=lambda r: r.path.stem)
def test_passes_validation_without_warnings(report):
    assert report.errors == []
    assert report.warnings == []
