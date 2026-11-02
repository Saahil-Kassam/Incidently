"""Generating scenarios from a theme: drafting, checking, and revising."""

import pytest

from incidently.authoring import DRAFT_ATTEMPTS, AuthoringError, check, generate
from incidently.catalog import CUSTOM_CATEGORY, Catalog
from incidently.config import Settings
from incidently.fake_llm import FakeLLM
from incidently.llm import LLMUnavailableError, Usage
from incidently.offline import offline_reply
from incidently.replies import CriterionDraft, HostDraft, PlaceholderDraft, ScenarioDraft

USAGE = Usage(input_tokens=1000, output_tokens=4000)


def draft(**changes) -> ScenarioDraft:
    fields = dict(
        title="Branch Office Can't Reach the Data Centre",
        topic="BGP",
        summary="Users at {{site}} lost access to data centre apps.",
        description="Users at a branch office can't reach anything in the data centre. You have a "
        "shell on the branch edge router. Find out why and restore connectivity.",
        skills=["Routing", "Cisco IOS"],
        placeholders=[PlaceholderDraft(name="site", values=["Leeds", "York"])],
        objective="Restore connectivity from {{site}} to the data centre.",
        hosts=[
            HostDraft(
                name="edge-rtr1",
                os="Cisco IOS XE 17.9",
                shell="cisco-ios",
                role="Branch edge router for {{site}}",
                ip="10.20.0.1",
                user="netops",
                cwd="",
            )
        ],
        start_host="edge-rtr1",
        root_cause_variants=[
            "The BGP neighbor's password was changed on one side only.",
            "An outbound prefix-list filters the branch subnet.",
        ],
        hidden_flag="The BGP session state and the log message that explains it.",
        resolution_condition="The session is established and routes are exchanged.",
        grading=[
            CriterionDraft(id="check_bgp", description="Check BGP", points=30, required=False),
            CriterionDraft(id="find_cause", description="Find the cause", points=40, required=True),
            CriterionDraft(id="fix", description="Fix and verify", points=30, required=True),
        ],
        hints=["Look at the routing protocol.", "Check the neighbor state and logs."],
        simulation_notes="The session dropped at 09:12.",
    )
    fields.update(changes)
    return ScenarioDraft(**fields)


@pytest.fixture
def catalog(tmp_path):
    return Catalog.load(custom_dir=tmp_path / "custom")


def test_generates_a_custom_scenario(catalog):
    llm = FakeLLM(draft(), usage=USAGE, model="claude-opus-5-5")
    settings = Settings()

    result = generate(llm, settings, catalog, theme="  BGP   flap ", difficulty="advanced")

    s = result.scenario
    assert s.id == "branch-office-can-t-reach-the-data-centre"
    assert s.category == CUSTOM_CATEGORY
    assert s.difficulty == "advanced"
    assert s.randomize == {"site": ["Leeds", "York"]}
    assert result.warnings == []
    assert result.calls == [("claude-opus-5-5", USAGE)]
    assert result.usage == USAGE
    assert result.estimated_cost() == pytest.approx((1000 * 4 + 4000 * 20) / 1_000_000)

    (request,) = llm.requests
    assert request.effort == settings.deep_effort
    assert request.cache
    assert "<theme>BGP flap</theme>" in request.messages[0]["content"]
    assert "Difficulty: advanced" in request.messages[0]["content"]
    # Existing titles and skills steer the model away from duplicates and towards the vocabulary.
    assert all(s.title in request.messages[0]["content"] for s in catalog)
    assert "Log analysis" in request.messages[0]["content"]
    assert "<example>" in request.system


def test_new_id_avoids_existing_ids(catalog):
    existing = next(iter(catalog))
    llm = FakeLLM(draft(title=existing.title))
    result = generate(llm, Settings(), catalog, theme="x", difficulty="beginner")
    assert result.scenario.id not in catalog
    assert catalog.new_id("Branch Office") == "branch-office"
    assert catalog.new_id(existing.id) == f"{existing.id}-2"
    assert catalog.new_id("!!!") == "custom-incident"


def test_invalid_draft_is_sent_back_with_its_problems(catalog):
    bad = draft(start_host="nowhere")
    llm = FakeLLM(bad, draft(), usage=USAGE)

    result = generate(llm, Settings(), catalog, theme="BGP", difficulty="intermediate")

    assert result.scenario.start_host == "edge-rtr1"
    assert len(result.calls) == 2
    revision = llm.requests[1].messages
    assert [m["role"] for m in revision] == ["user", "assistant", "user"]
    assert revision[1]["content"] == bad.model_dump_json()
    assert "start_host 'nowhere'" in revision[2]["content"]


def test_style_warnings_are_revised_then_accepted(catalog):
    weak = draft(hints=["Only one hint."])
    llm = FakeLLM(*[weak] * DRAFT_ATTEMPTS)

    result = generate(llm, Settings(), catalog, theme="BGP", difficulty="beginner")

    assert len(llm.requests) == DRAFT_ATTEMPTS
    assert "only 1 hint" in llm.requests[1].messages[-1]["content"]
    assert result.warnings == ["only 1 hint; give at least 2"]


def test_a_leaky_description_is_an_error(catalog):
    leaky = draft(description="The BGP neighbor's password was changed on one side only. Fix it.")
    llm = FakeLLM(*[leaky] * DRAFT_ATTEMPTS)
    calls = []

    with pytest.raises(AuthoringError, match="gives away hidden text"):
        generate(llm, Settings(), catalog, theme="BGP", difficulty="beginner", calls=calls)
    assert len(calls) == DRAFT_ATTEMPTS  # spent tokens are reported even on failure


def test_model_failure_keeps_earlier_calls(catalog):
    llm = FakeLLM(draft(placeholders=[]), LLMUnavailableError("busy"), usage=USAGE)
    calls = []
    with pytest.raises(LLMUnavailableError):
        generate(llm, Settings(), catalog, theme="BGP", difficulty="beginner", calls=calls)
    assert calls == [("fake-llm", USAGE)]


@pytest.mark.parametrize("theme", ["", "   ", "x" * 201])
def test_theme_is_required_and_bounded(catalog, theme):
    llm = FakeLLM()
    with pytest.raises(AuthoringError):
        generate(llm, Settings(), catalog, theme=theme, difficulty="beginner")
    assert llm.requests == []


def test_duplicate_placeholder_names(catalog):
    twice = [PlaceholderDraft(name="site", values=[v]) for v in ("A", "B")]
    scenario, errors, _ = check(draft(placeholders=twice), "x", "beginner")
    assert scenario is None
    assert errors == ["placeholder names must be unique; duplicated: ['site']"]


def test_offline_mode_writes_a_valid_placeholder(catalog):
    llm = FakeLLM(default=offline_reply)
    settings = Settings(fake_llm=True)
    result = generate(llm, settings, catalog, theme="VLAN mismatch", difficulty="beginner")
    assert result.scenario.title == "Offline Practice: VLAN mismatch"
    assert result.warnings == []
    assert result.usage == Usage()
