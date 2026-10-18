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


LAB_SCENARIO = {
    **MINIMAL_SCENARIO,
    "id": "lab-outage",
    "randomize": {},
    "summary": "Users can't resolve names.",
    "objective": "Restore name resolution.",
    "resolution_condition": "Names resolve again.",
    "hosts": [
        {
            "name": "jump01",
            "os": "Ubuntu Server 24.04 LTS",
            "shell": "bash",
            "role": "Jump host",
            "ip": "10.0.0.10",
            "user": "netops",
            "cwd": "/home/netops",
        },
        {
            "name": "core-rtr1",
            "os": "Cisco IOS XE 17.9",
            "shell": "cisco-ios",
            "role": "Core router",
            "ip": "10.0.0.1",
            "user": "netops",
            "cwd": "",
        },
        {
            "name": "dc01",
            "os": "Windows Server 2022",
            "shell": "powershell",
            "role": "Domain controller and DNS",
            "ip": "10.0.0.53",
            "user": "CORP\\netops",
            "cwd": "C:\\Users\\netops",
        },
    ],
    "start_host": "jump01",
}


@pytest.fixture
def lab_instance():
    """A playable instance with one host per shell: bash, Cisco IOS, PowerShell."""
    from incidently.catalog import ScenarioInstance
    from incidently.scenario import Scenario

    scenario = Scenario.model_validate(copy.deepcopy(LAB_SCENARIO))
    return ScenarioInstance(scenario, scenario.root_cause_variants[0], seed=7, values={})


@pytest.fixture
def bootstrap():
    from incidently.replies import Bootstrap, Ticket

    return Bootstrap(
        ticket=Ticket(
            id="INC0012345",
            priority="P2",
            reported_by="Service desk",
            summary="Name resolution failing",
            description="Users report websites not loading.",
        ),
        facts=["Resolver is unbound 1.19.2", "Upstream forwarder is 192.0.2.53"],
    )


@pytest.fixture
def session(lab_instance, bootstrap):
    from incidently.session import Session

    return Session.create(lab_instance, bootstrap)


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
