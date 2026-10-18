import pytest

from incidently.catalog import Catalog, ScenarioError, instantiate, load_scenario
from incidently.scenario import PLACEHOLDER, Scenario


def test_load_directory(write_scenario, scenario_data, tmp_path):
    write_scenario(scenario_data)
    second = dict(scenario_data, id="another-outage")
    write_scenario(second)

    catalog = Catalog.load(tmp_path)

    assert len(catalog) == 2
    assert [s.id for s in catalog] == ["another-outage", "test-outage"]
    assert "test-outage" in catalog
    assert catalog.get("test-outage").title == "Test Outage"


def test_unknown_scenario_id(write_scenario, scenario_data, tmp_path):
    write_scenario(scenario_data)
    with pytest.raises(ScenarioError, match="unknown scenario: 'nope'"):
        Catalog.load(tmp_path).get("nope")


def test_missing_directory(tmp_path):
    with pytest.raises(ScenarioError, match="scenario directory not found"):
        Catalog.load(tmp_path / "missing")


def test_empty_directory(tmp_path):
    with pytest.raises(ScenarioError, match="no scenario files"):
        Catalog.load(tmp_path)


def test_id_must_match_file_name(write_scenario, scenario_data):
    path = write_scenario(scenario_data, name="other-name")
    with pytest.raises(ScenarioError, match="other-name.yaml: id 'test-outage' must match"):
        load_scenario(path)


def test_invalid_yaml_names_the_file(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("id: [unclosed\n", encoding="utf-8")
    with pytest.raises(ScenarioError, match="broken.yaml: cannot read YAML"):
        load_scenario(path)


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    path = tmp_path / "dupes.yaml"
    path.write_text("id: dupes\nid: dupes\n", encoding="utf-8")
    with pytest.raises(ScenarioError, match="dupes.yaml: cannot read YAML"):
        load_scenario(path)


def test_top_level_must_be_a_mapping(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ScenarioError, match="list.yaml: top level must be a mapping"):
        load_scenario(path)


def test_schema_errors_name_the_file(write_scenario, scenario_data):
    scenario_data["difficulty"] = "expert"
    path = write_scenario(scenario_data)
    with pytest.raises(ScenarioError, match=r"(?s)test-outage.yaml: .*difficulty"):
        load_scenario(path)


def test_yaml_tags_cannot_build_objects(tmp_path):
    path = tmp_path / "evil.yaml"
    path.write_text("id: !!python/object/apply:os.system ['echo hi']\n", encoding="utf-8")
    with pytest.raises(ScenarioError, match="evil.yaml: cannot read YAML"):
        load_scenario(path)


# --- instantiate ----------------------------------------------------------


@pytest.fixture
def scenario(scenario_data):
    return Scenario.model_validate(scenario_data)


def test_instantiate_fills_every_placeholder(scenario):
    instance = instantiate(scenario, seed=1)
    resolved = instance.scenario

    assert not PLACEHOLDER.search(resolved.model_dump_json())
    assert resolved.randomize == {}
    assert instance.values["site"] in {"HQ", "Annex"}
    assert resolved.objective == f"Restore name resolution at {instance.values['site']}."
    assert resolved.hosts[0].ip == f"10.{instance.values['octet']}.0.53"
    assert instance.root_cause in scenario.root_cause_variants


def test_same_seed_gives_same_instance(scenario):
    assert instantiate(scenario, seed=42) == instantiate(scenario, seed=42)


def test_seeds_cover_every_choice(scenario):
    instances = [instantiate(scenario, seed=s) for s in range(50)]
    assert {i.values["site"] for i in instances} == {"HQ", "Annex"}
    assert {i.root_cause for i in instances} == set(scenario.root_cause_variants)


def test_random_seed_is_recorded_and_replayable(scenario):
    instance = instantiate(scenario)
    assert instantiate(scenario, seed=instance.seed) == instance


def test_template_is_not_modified(scenario):
    before = scenario.model_dump()
    instantiate(scenario, seed=3)
    assert scenario.model_dump() == before
