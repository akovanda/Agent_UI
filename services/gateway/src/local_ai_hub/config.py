from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str | None = None
    llama_base_url: str = "http://llama-router:8080"
    llama_api_key: SecretStr = SecretStr("")
    gateway_api_key: SecretStr = SecretStr("local-development-key")
    default_user_id: str = "andrew"
    profile_config_path: Path = Path("config/gateway/profiles.yaml")
    model_coordinator_mode: Literal["explicit", "autoload", "none"] = "explicit"
    fixed_backend_model: str | None = None
    model_load_timeout_seconds: float = 360.0
    model_poll_interval_seconds: float = 1.0
    upstream_connect_timeout_seconds: float = 10.0
    upstream_write_timeout_seconds: float = 60.0
    gpu_max_concurrent_requests: int = Field(default=1, ge=1, le=8)
    memory_enabled: bool = True
    memory_required: bool = False
    memory_top_k: int = Field(default=6, ge=0, le=30)
    memory_max_chars: int = Field(default=6000, ge=0, le=50000)
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:8001,http://127.0.0.1:8001"
    )
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_single_gpu_coordination(self) -> Settings:
        if self.model_coordinator_mode == "explicit" and self.gpu_max_concurrent_requests != 1:
            raise ValueError(
                "explicit one-model coordination requires GPU_MAX_CONCURRENT_REQUESTS=1"
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def llama_api_base_url(self) -> str:
        return f"{self.llama_base_url.rstrip('/')}/v1"


class MemoryProfile(BaseModel):
    enabled: bool = False
    namespaces: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    advertised: bool = False
    route: Literal["auto"] | None = None
    backend_model: str | None = None
    description: str = ""
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    system_prompt: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    memory: MemoryProfile = Field(default_factory=MemoryProfile)

    @model_validator(mode="after")
    def validate_target(self) -> Profile:
        if self.route == "auto" and self.backend_model is not None:
            raise ValueError("automatic profiles cannot define backend_model")
        if self.route is None and not self.backend_model:
            raise ValueError("concrete profiles must define backend_model")
        return self


class Backend(BaseModel):
    description: str = ""


class ProfileDocument(BaseModel):
    version: Literal[1]
    backends: dict[str, Backend]
    profiles: dict[str, Profile]

    @model_validator(mode="after")
    def validate_references(self) -> ProfileDocument:
        unknown = {
            profile.backend_model
            for profile in self.profiles.values()
            if profile.backend_model and profile.backend_model not in self.backends
        }
        if unknown:
            raise ValueError(f"profiles reference unknown backends: {sorted(unknown)}")
        return self


_SHORTHAND_RESERVED_KEYS = {
    "advertised",
    "backend_model",
    "defaults",
    "description",
    "memory",
    "model",
    "reasoning_effort",
    "route",
    "system_prompt",
}


def _expand_shorthand_profiles(raw: Any) -> dict[str, Any]:
    """Convert the model catalog's compact profile map into gateway configuration.

    The Docker and Helm control planes generate ``profiles.json`` as a mapping of
    virtual profile names to a model id and sampler values. Keeping this adapter in
    the gateway lets operators add models without editing Python or maintaining a
    second profile document. The original full ``ProfileDocument`` format remains
    supported for advanced installations.
    """

    if not isinstance(raw, dict):
        raise RuntimeError("profile configuration must be a mapping")

    if {"version", "backends", "profiles"}.issubset(raw):
        return raw

    model_metadata: dict[str, Any] = {}
    if isinstance(raw.get("profiles"), dict):
        profiles_raw = raw["profiles"]
        if isinstance(raw.get("models"), dict):
            model_metadata = raw["models"]
    else:
        profiles_raw = raw

    backends: dict[str, dict[str, str]] = {}
    profiles: dict[str, dict[str, Any]] = {}
    for profile_id, value in profiles_raw.items():
        if not isinstance(value, dict):
            raise RuntimeError(f"profile {profile_id} must be a mapping")

        route = value.get("route")
        backend_model = value.get("backend_model") or value.get("model")
        profile: dict[str, Any] = {
            "advertised": bool(value.get("advertised", True)),
            "description": str(value.get("description", profile_id)),
        }

        if route == "auto":
            profile["route"] = "auto"
        else:
            if not isinstance(backend_model, str) or not backend_model:
                raise RuntimeError(f"profile {profile_id} does not select a model")
            profile["backend_model"] = backend_model
            metadata = model_metadata.get(backend_model, {})
            description = metadata.get("description", "") if isinstance(metadata, dict) else ""
            backends.setdefault(backend_model, {"description": str(description)})

        reasoning_effort = value.get("reasoning_effort")
        if reasoning_effort in {"low", "medium", "high"}:
            profile["reasoning_effort"] = reasoning_effort

        if isinstance(value.get("system_prompt"), str):
            profile["system_prompt"] = value["system_prompt"]

        explicit_defaults = value.get("defaults", {})
        if explicit_defaults is None:
            explicit_defaults = {}
        if not isinstance(explicit_defaults, dict):
            raise RuntimeError(f"profile {profile_id} defaults must be a mapping")
        defaults = dict(explicit_defaults)
        defaults.update(
            {key: item for key, item in value.items() if key not in _SHORTHAND_RESERVED_KEYS}
        )
        profile["defaults"] = defaults

        memory = value.get("memory")
        if memory is not None:
            if not isinstance(memory, dict):
                raise RuntimeError(f"profile {profile_id} memory must be a mapping")
            profile["memory"] = memory

        profiles[str(profile_id)] = profile

    return {"version": 1, "backends": backends, "profiles": profiles}


def load_profiles(path: Path) -> ProfileDocument:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"profile configuration does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"invalid profile YAML in {path}: {exc}") from exc
    return ProfileDocument.model_validate(_expand_shorthand_profiles(raw))
