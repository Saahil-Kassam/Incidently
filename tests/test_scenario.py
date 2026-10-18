import re

import pytest
from pydantic import ValidationError

from incidently.scenario import Scenario


def test_valid_scenario_loads(scenario_data):
    scenario = Scenario.model_validate(scenario_data)
    assert scenario.max_points == 100
    assert scenario.host("DNS01").name == "dns01"
    assert scenario.host("missing") is None
    assert set(scenario.placeholders()) == {"site", "octet"}


def test_trailing_whitespace_from_folded_yaml_is_stripped(scenario_data):
    scenario_data["objective"] = "Restore name resolution at {{site}}.\n"
    assert Scenario.model_validate(scenario_data).objective.endswith(".")


def test_numeric_randomize_values_become_strings(scenario_data):
    scenario_data["randomize"]["octet"] = [10, 20.5]
    assert Scenario.model_validate(scenario_data).randomize["octet"] == ["10", "20.5"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(colour="blue"), "Extra inputs are not permitted"),
        (lambda d: d.update(id="Bad_ID"), "String should match pattern"),
        (lambda d: d.update(difficulty="expert"), "Input should be 'beginner'"),
        (lambda d: d.update(hosts=[]), "at least 1 item"),
        (lambda d: d.update(start_host="web01"), "start_host 'web01' is not one of"),
        (lambda d: d["hosts"].append(dict(d["hosts"][0])), "host names must be unique"),
        (lambda d: d["hosts"][0].update(shell="zsh"), "Input should be 'bash'"),
        (lambda d: d.update(root_cause_variants=[]), "at least 1 item"),
        (lambda d: d.update(hints=[]), "at least 1 item"),
        (lambda d: d["grading"].append(dict(d["grading"][0])), "criterion ids must be unique"),
        (lambda d: d["grading"][0].update(points=0), "greater than 0"),
        (lambda d: d["grading"][0].update(id="Find-Cause"), "String should match pattern"),
        (
            lambda d: d["grading"][0].update(required=False),
            "at least one grading criterion must be required",
        ),
        (lambda d: d["randomize"].update(site=[]), "randomize.site must list at least one value"),
        (lambda d: d["randomize"].update(site=["HQ", "HQ"]), "randomize.site values"),
        (lambda d: d["randomize"].update(unused=["x"]), "never used in the scenario: ['unused']"),
        (lambda d: d["randomize"].pop("octet"), "used but not declared in randomize: ['octet']"),
        (lambda d: d["randomize"].update({"bad-key": ["x"]}), "not a valid placeholder name"),
    ],
)
def test_invalid_scenarios_are_rejected(scenario_data, mutate, message):
    mutate(scenario_data)
    with pytest.raises(ValidationError, match=re.escape(message)):
        Scenario.model_validate(scenario_data)


def test_scenario_is_immutable(scenario_data):
    scenario = Scenario.model_validate(scenario_data)
    with pytest.raises(ValidationError):
        scenario.title = "Changed"
