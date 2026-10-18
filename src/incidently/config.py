"""Application settings, read from environment variables.

Values come from the process environment, with a local ``.env`` file filling
in anything not already set (see ``.env.example``). Every setting has a safe
default except the API key, which the Anthropic SDK looks up itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from dotenv import load_dotenv
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
}


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

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build settings from ``environ`` (default: ``.env`` plus ``os.environ``)."""
        if environ is None:
            load_dotenv(override=False)  # real environment variables win over .env
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
