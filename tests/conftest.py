import copy
from pathlib import Path

import pytest
from ruamel.yaml import YAML

MINIMAL_SCENARIO = {
    "id": "test-outage",
    "title": "Test Outage",
    "category": "Networking",
    "topic": "DNS",
    "difficulty": "beginner",
    "summary": "Users at {{site}} can't resolve names.",
    "randomize": {"site": ["HQ", "Annex"], "octet": ["10", "20"]},
    "objective": "Restore name resolution at {{site}}.",
    "hosts": [
        {
            "name": "dns01",
            "os": "Ubuntu Server 24.04 LTS",
            "shell": "bash",
            "role": "Resolver for {{site}}",
            "ip": "10.{{octet}}.0.53",
            "user": "netadmin",
            "cwd": "/home/netadmin",
        }
    ],
    "start_host": "dns01",
    "root_cause_variants": ["Forwarder points at a dead upstream.", "Zone file syntax error."],
    "hidden_flag": "The resolver log shows the failing upstream.",
    "resolution_condition": "Names resolve again from {{site}}.",
    "grading": [
        {"id": "find_cause", "description": "Find the cause", "points": 60, "required": True},
        {"id": "verify_fix", "description": "Verify the fix", "points": 40, "required": False},
    ],
    "hints": ["Check the resolver logs."],
    "simulation_notes": "Keep output realistic.",
}


@pytest.fixture
def scenario_data():
    """A fresh, valid scenario mapping that each test can modify."""
    return copy.deepcopy(MINIMAL_SCENARIO)


@pytest.fixture
def write_scenario(tmp_path):
    """Write a scenario mapping to ``<tmp>/<name>.yaml`` and return its path."""

    def write(data, name=None) -> Path:
        path = tmp_path / f"{name or data['id']}.yaml"
        with path.open("w", encoding="utf-8") as f:
            YAML(typ="safe", pure=True).dump(data, f)
        return path

    return write
