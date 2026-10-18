import pytest

from incidently.config import DEFAULT_MODEL, ConfigError, Settings


def test_defaults():
    settings = Settings.from_env({})
    assert settings.model == DEFAULT_MODEL
    assert settings.turn_effort == "low"
    assert settings.deep_effort == "high"
    assert settings.fake_llm is False
    assert settings.max_retries == 4
    assert settings.timeout == 120.0


def test_reads_environment_variables():
    settings = Settings.from_env(
        {
            "INCIDENTLY_MODEL": "claude-sonnet-5-5",
            "INCIDENTLY_TURN_EFFORT": "medium",
            "INCIDENTLY_DEEP_EFFORT": "xhigh",
            "INCIDENTLY_FAKE_LLM": "1",
            "INCIDENTLY_API_MAX_RETRIES": "2",
            "INCIDENTLY_API_TIMEOUT": "30.5",
        }
    )
    assert settings.model == "claude-sonnet-5-5"
    assert settings.turn_effort == "medium"
    assert settings.deep_effort == "xhigh"
    assert settings.fake_llm is True
    assert settings.max_retries == 2
    assert settings.timeout == 30.5


def test_blank_values_fall_back_to_defaults():
    settings = Settings.from_env({"INCIDENTLY_MODEL": "  ", "INCIDENTLY_FAKE_LLM": ""})
    assert settings.model == DEFAULT_MODEL
    assert settings.fake_llm is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_fake_llm_truthy_values(value):
    assert Settings.from_env({"INCIDENTLY_FAKE_LLM": value}).fake_llm is True


@pytest.mark.parametrize("value", ["0", "false", "No", "off"])
def test_fake_llm_falsy_values(value):
    assert Settings.from_env({"INCIDENTLY_FAKE_LLM": value}).fake_llm is False


@pytest.mark.parametrize(
    ("env", "names"),
    [
        ({"INCIDENTLY_TURN_EFFORT": "extreme"}, "INCIDENTLY_TURN_EFFORT"),
        ({"INCIDENTLY_FAKE_LLM": "maybe"}, "INCIDENTLY_FAKE_LLM"),
        ({"INCIDENTLY_API_MAX_RETRIES": "-1"}, "INCIDENTLY_API_MAX_RETRIES"),
        ({"INCIDENTLY_API_TIMEOUT": "soon"}, "INCIDENTLY_API_TIMEOUT"),
    ],
)
def test_invalid_values_name_the_variable(env, names):
    with pytest.raises(ConfigError, match=names):
        Settings.from_env(env)


def test_settings_are_immutable():
    settings = Settings.from_env({})
    with pytest.raises(ValueError):
        settings.model = "other"
