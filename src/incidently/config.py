"""Application settings, read from environment variables.

Values come from the process environment, with a local ``.env`` file filling
in anything not already set (see ``.env.example``). Every setting has a safe
default except the API key, which the Anthropic SDK looks up itself.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Effort = Literal["low", "medium", "high", "xhigh", "max"]

DEFAULT_MODEL = "claude-opus-5-5"

# Environment variable -> Settings field.
_ENV_VARS = {
    "INCIDENTLY_MODEL": "model",
    "INCIDENTLY_TURN_EFFORT": "turn_effort",
    "INCIDENTLY_DEEP_EFFORT": "deep_effort",
    "INCIDENTLY_FAKE_LLM": "fake_llm",
    "INCIDENTLY_API_MAX_RETRIES": "max_retries",
    "INCIDENTLY_API_TIMEOUT": "timeout",
    "INCIDENTLY_DATA_DIR": "data_dir",
}


def default_data_dir() -> Path:
    """Where saved sessions live unless ``INCIDENTLY_DATA_DIR`` says otherwise.

    The per-user application data folder for the platform: ``%LOCALAPPDATA%``
    on Windows, ``~/Library/Application Support`` on macOS, and
    ``$XDG_DATA_HOME`` (default ``~/.local/share``) elsewhere.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "Incidently"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Incidently"
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "incidently"


class ConfigError(Exception):
    """A setting has an invalid value."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(default=DEFAULT_MODEL, min_length=1)
    turn_effort: Effort = Field(
        default="low", description="Effort for each simulated command (speed matters most)"
    )
    deep_effort: Effort = Field(
        default="high", description="Effort for setup and final grading (quality matters most)"
    )
    fake_llm: bool = Field(
        default=False, description="Use the scripted fake model instead of the real API"
    )
    max_retries: int = Field(
        default=4, ge=0, le=10, description="SDK retries for rate limits, 5xx, and network errors"
    )
    timeout: float = Field(default=120.0, gt=0, description="Seconds per API request")
    data_dir: Path | None = Field(
        default=None, description="Folder for saved sessions (default: see default_data_dir)"
    )

    @property
    def database_path(self) -> Path:
        """The SQLite file for saved sessions.

        Offline mode keeps its own file, so placeholder sessions never mix
        with real ones or count towards progress.
        """
        name = "incidently-offline.db" if self.fake_llm else "incidently.db"
        return (self.data_dir or default_data_dir()) / name

    @field_validator("fake_llm", mode="before")
    @classmethod
    def _parse_bool(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"", "0", "false", "no", "off"}:
                return False
        return value

    @field_validator("data_dir")
    @classmethod
    def _expand_home(cls, value: Path | None) -> Path | None:
        return value.expanduser() if value is not None else None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``environ`` (default: ``.env`` plus ``os.environ``)."""
        if environ is None:
            # Search from the working directory rather than from this module's
            # install location, so the .env where the app is started is found.
            # Real environment variables win over values in the file.
            load_dotenv(find_dotenv(usecwd=True), override=False)
            environ = os.environ
        values = {
            field: environ[var].strip()
            for var, field in _ENV_VARS.items()
            if environ.get(var, "").strip()
        }
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            field_to_var = {field: var for var, field in _ENV_VARS.items()}
            problems = "; ".join(
                f"{field_to_var.get(str(err['loc'][0]), err['loc'][0])}: {err['msg']}"
                for err in exc.errors()
            )
            raise ConfigError(f"invalid configuration: {problems}") from exc
