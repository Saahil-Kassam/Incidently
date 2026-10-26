import os
from pathlib import Path

import pytest

from incidently.config import DEFAULT_MODEL, ConfigError, Settings, default_data_dir


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


def test_dotenv_is_found_in_the_working_directory(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("INCIDENTLY_TURN_EFFORT=medium\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INCIDENTLY_TURN_EFFORT", raising=False)
    try:
        assert Settings.from_env().turn_effort == "medium"
    finally:
        os.environ.pop("INCIDENTLY_TURN_EFFORT", None)  # load_dotenv wrote it


def test_settings_are_immutable():
    settings = Settings.from_env({})
    with pytest.raises(ValueError):
        settings.model = "other"


# --- where sessions are saved ---------------------------------------------------


def test_data_dir_from_environment_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    settings = Settings.from_env({"INCIDENTLY_DATA_DIR": "~/saves"})
    assert settings.data_dir == tmp_path / "saves"
    assert settings.database_path == tmp_path / "saves" / "incidently.db"


def test_offline_mode_has_its_own_database(tmp_path):
    real = Settings(data_dir=tmp_path)
    offline = Settings(data_dir=tmp_path, fake_llm=True)
    assert real.database_path == tmp_path / "incidently.db"
    assert offline.database_path == tmp_path / "incidently-offline.db"


def test_database_defaults_to_the_platform_data_dir(isolated_data_dir):
    assert Settings().database_path == isolated_data_dir / "incidently.db"


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("win32", {"LOCALAPPDATA": "/local"}, "/local/Incidently"),
        ("darwin", {}, "~/Library/Application Support/Incidently"),
        ("linux", {"XDG_DATA_HOME": "/xdg"}, "/xdg/incidently"),
        ("linux", {}, "~/.local/share/incidently"),
    ],
)
def test_default_data_dir_per_platform(monkeypatch, platform, env, expected):
    monkeypatch.setattr("sys.platform", platform)
    for var in ("LOCALAPPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert default_data_dir() == Path(expected).expanduser()
