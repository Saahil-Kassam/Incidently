"""``incidently validate``: errors that break a scenario, warnings that weaken it."""

import copy
import io

import pytest

from incidently import __main__ as cli
from incidently.validate import main, validate


@pytest.fixture
def good(scenario_data):
    """A scenario that passes with no warnings."""
    data = copy.deepcopy(scenario_data)
    data["description"] = "Name resolution is failing for one site. Find out why and fix it."
    data["skills"] = ["DNS", "Log analysis"]
    data["hints"] = ["Check the resolver logs.", "Compare the forwarders with the upstream."]
    return data


def run(paths, strict=False):
    out, err = io.StringIO(), io.StringIO()
    code = main(paths, strict=strict, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_a_good_scenario_passes(good, write_scenario):
    path = write_scenario(good)
    (report,) = validate([path])
    assert report.ok and report.warnings == []

    code, out, err = run([path])
    assert code == 0
    assert "ok     test-outage.yaml: Networking / beginner, 1 host(s), 2 root causes" in out
    assert err == ""


def test_folders_are_searched_for_yaml(good, write_scenario, tmp_path):
    write_scenario(good)
    write_scenario({**good, "id": "second-outage"})
    (tmp_path / "notes.txt").write_text("not a scenario")
    assert [r.path.name for r in validate([tmp_path])] == [
        "second-outage.yaml",
        "test-outage.yaml",
    ]


# --- errors ------------------------------------------------------------------------------


def test_unparseable_yaml(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("id: [unclosed\n")
    (report,) = validate([path])
    assert "cannot read YAML" in report.errors[0]


def test_schema_violation(good, write_scenario):
    path = write_scenario({**good, "difficulty": "expert"})
    code, _, err = run([path])
    assert code == 1
    assert "error  test-outage.yaml:" in err and "difficulty" in err


def test_id_must_match_the_file_name(good, write_scenario):
    path = write_scenario(good, name="other-name")
    (report,) = validate([path])
    assert "must match the file name" in report.errors[0]


def test_ids_must_be_unique_across_files(good, write_scenario, tmp_path):
    first = write_scenario(good)
    elsewhere = tmp_path / "copy"
    elsewhere.mkdir()
    second = elsewhere / first.name
    second.write_text(first.read_text())
    reports = validate([first, second])
    assert reports[0].ok
    assert "also used by test-outage.yaml" in reports[1].errors[0]


def test_only_yaml_files(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text("{}")
    (report,) = validate([path])
    assert report.errors == ["not a .yaml file"]


def test_nothing_to_check(tmp_path):
    code, _, err = run([tmp_path])
    assert code == 1 and "no scenario files" in err


# --- warnings -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "warning"),
    [
        (lambda d: d["grading"][1].update(points=30), "grading points total 90, not 100"),
        (lambda d: d.update(root_cause_variants=["Only one cause."]), "only 1 root cause"),
        (lambda d: d.update(root_cause_variants=["Same.", "Same."]), "two root cause variants"),
        (lambda d: d.update(hints=["Just one."]), "only 1 hint"),
        (lambda d: d.update(description=""), "no description"),
        (lambda d: d.update(description="Short."), "should say more than the summary"),
        (lambda d: d.update(skills=["DNS"]), "1 skills; list 2 to 5"),
        (
            lambda d: d.update(
                description="Users are down because the forwarder points at a dead upstream today."
            ),
            "gives away hidden text: 'forwarder points at a dead'",
        ),
    ],
)
def test_house_style_warnings(good, write_scenario, change, warning):
    change(good)
    (report,) = validate([write_scenario(good)])
    assert report.ok
    assert any(warning in w for w in report.warnings), report.warnings


def test_warnings_fail_only_when_strict(good, write_scenario):
    path = write_scenario({**good, "skills": []})
    code, _, err = run([path])
    assert code == 0 and "warn   test-outage.yaml: 0 skills" in err
    assert run([path], strict=True)[0] == 1


# --- command line ------------------------------------------------------------------------


def test_cli_validates_the_bundled_scenarios(capsys):
    assert cli.main(["validate"]) == 0
    out = capsys.readouterr().out
    assert "8 file(s): 0 with errors, 0 with warnings" in out


def test_cli_passes_paths_and_strict(good, write_scenario, capsys):
    path = write_scenario({**good, "skills": []})
    assert cli.main(["validate", str(path)]) == 0
    assert cli.main(["validate", "--strict", str(path)]) == 1
