from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str | None = None
    llama_base_url: str = "http://llama:8080"
    llama_api_key: SecretStr = SecretStr("")
    gateway_api_key: SecretStr = SecretStr("local-development-key")
    principal_api_keys_json: str = ""
    default_user_id: str = "local-user"
    profile_config_path: Path = Path("config/models/catalog.yaml")
    model_coordinator_mode: Literal["explicit", "autoload", "none"] = "explicit"
    fixed_backend_model: str | None = None
    model_load_timeout_seconds: float = 360.0
    model_poll_interval_seconds: float = 1.0
    upstream_connect_timeout_seconds: float = 10.0
    upstream_write_timeout_seconds: float = 60.0
    gpu_max_concurrent_requests: int = Field(default=1, ge=1, le=32)
    memory_enabled: bool = True
    memory_required: bool = False
    memory_top_k: int = Field(default=6, ge=0, le=30)
    memory_max_chars: int = Field(default=6000, ge=0, le=50000)
    memory_config_path: Path = Path("config/memory/base.yaml")
    memory_config_overlay_path: Path | None = None
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8001,http://127.0.0.1:8001"
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


class ReasoningFeature(BaseModel):
    """How a model accepts an effort/reasoning control.

    ``values`` maps stable experience values (for example ``fast`` or ``deep``)
    to whatever the upstream model expects. Empty mappings pass values through.
    """

    supported: bool = True
    request_field: str = "reasoning_effort"
    transport: Literal["body", "chat_template_kwargs"] = "body"
    values: dict[str, Any] = Field(default_factory=dict)
    unsupported_policy: Literal["ignore", "reject"] = "ignore"


class ModelFeatures(BaseModel):
    reasoning: ReasoningFeature | None = None
    tools: bool | None = None
    structured_output: bool | None = None
    vision: bool | None = None
    audio: bool | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ArtifactSpec(BaseModel):
    """Location of local weights without prescribing where operators store them."""

    kind: Literal[
        "managed",
        "host_path",
        "docker_volume",
        "container_path",
        "pvc",
        "hostPath",
        "none",
    ] = "none"
    path: str | None = None
    filename: str | None = None
    volume: str | None = None
    claim_name: str | None = None
    sub_path: str | None = None
    read_only: bool = True

    @model_validator(mode="after")
    def validate_location(self) -> ArtifactSpec:
        if self.kind == "host_path" and not self.path:
            raise ValueError("host_path artifacts require path")
        if self.kind == "docker_volume" and not self.volume:
            raise ValueError("docker_volume artifacts require volume")
        if self.kind == "container_path" and not self.path:
            raise ValueError("container_path artifacts require path")
        if self.kind == "pvc" and not self.claim_name:
            raise ValueError("pvc artifacts require claim_name")
        if self.kind == "hostPath" and not self.path:
            raise ValueError("hostPath artifacts require path")
        return self


class Backend(BaseModel):
    kind: Literal["llama.cpp", "openai-compatible", "comfyui"] = "openai-compatible"
    enabled: bool = True
    description: str = ""
    base_url: str | None = None
    base_url_env: str | None = None
    api_key_env: str | None = None
    coordinator: Literal["explicit", "autoload", "none"] = "none"
    serialize_requests: bool = False
    endpoints: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    @property
    def resolved_base_url(self) -> str | None:
        if self.base_url_env:
            return os.getenv(self.base_url_env) or self.base_url
        return self.base_url

    @property
    def resolved_api_key(self) -> str:
        return os.getenv(self.api_key_env, "") if self.api_key_env else ""


class ModelSpec(BaseModel):
    display_name: str = ""
    description: str = ""
    enabled: bool = True
    backend: str
    upstream_model: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    features: ModelFeatures = Field(default_factory=ModelFeatures)
    defaults: dict[str, Any] = Field(default_factory=dict)
    artifact: ArtifactSpec = Field(default_factory=ArtifactSpec)
    runtime: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities", mode="before")
    @classmethod
    def normalize_capabilities(cls, value: Any) -> Any:
        if isinstance(value, list):
            return {str(item): {} for item in value}
        return value

    def has_capability(self, capability: str) -> bool:
        value = self.capabilities.get(capability)
        return bool(value is not None and value is not False)


class Profile(BaseModel):
    """A stable, human-facing experience backed by a model capability."""

    advertised: bool = True
    route: Literal["auto"] | None = None
    backend_model: str | None = None
    model: str | None = None
    capability: str | None = None
    description: str = ""
    reasoning_effort: str | None = None
    system_prompt: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    memory: MemoryProfile = Field(default_factory=MemoryProfile)
    selection: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_target(self) -> Profile:
        if self.model and not self.backend_model:
            self.backend_model = self.model
        if self.route == "auto" and self.backend_model is not None:
            raise ValueError("automatic experiences cannot define a model")
        if self.route is None and not self.backend_model and not self.capability:
            raise ValueError("experiences require model, capability, or route: auto")
        return self


class ProfileDocument(BaseModel):
    version: Literal[1, 2]
    backends: dict[str, Backend] = Field(default_factory=dict)
    models: dict[str, ModelSpec] = Field(default_factory=dict)
    profiles: dict[str, Profile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> ProfileDocument:
        unknown_backends = {
            model.backend for model in self.models.values() if model.backend not in self.backends
        }
        if unknown_backends:
            raise ValueError(f"models reference unknown backends: {sorted(unknown_backends)}")
        unknown_models = {
            profile.backend_model
            for profile in self.profiles.values()
            if profile.backend_model and profile.backend_model not in self.models
        }
        if unknown_models:
            raise ValueError(f"experiences reference unknown models: {sorted(unknown_models)}")
        incompatible = []
        for profile_id, profile in self.profiles.items():
            if not profile.backend_model or not profile.capability:
                continue
            model = self.models.get(profile.backend_model)
            if model is not None and not model.has_capability(profile.capability):
                incompatible.append(
                    f"{profile_id} requires {profile.capability} from {profile.backend_model}"
                )
        if incompatible:
            raise ValueError(f"incompatible pinned experiences: {sorted(incompatible)}")
        return self


_SHORTHAND_RESERVED_KEYS = {
    "advertised",
    "backend_model",
    "capability",
    "defaults",
    "description",
    "memory",
    "model",
    "reasoning_effort",
    "route",
    "selection",
    "system_prompt",
}


def _expand_v2(raw: dict[str, Any]) -> dict[str, Any]:
    experiences = raw.get("experiences")
    if experiences is None:
        experiences = raw.get("profiles", {})
    return {
        "version": 2,
        "backends": raw.get("backends", {}),
        "models": raw.get("models", {}),
        "profiles": experiences,
    }


def _legacy_model_spec(name: str, metadata: Any) -> dict[str, Any]:
    details = metadata if isinstance(metadata, dict) else {}
    capabilities = details.get("capabilities") or {"chat": {}}
    if isinstance(capabilities, list):
        capabilities = {str(item): {} for item in capabilities}
    features = dict(details.get("features") or {})
    model_metadata = dict(details.get("metadata") or {})
    model_metadata.setdefault("advertise_direct", False)
    family = str(details.get("family", "")).lower()
    if family == "gpt-oss":
        features.setdefault(
            "reasoning",
            {
                "supported": True,
                "request_field": "reasoning_effort",
                "transport": "body",
                "values": {"none": None, "low": "low", "medium": "medium", "high": "high"},
                "unsupported_policy": "reject",
            },
        )
        model_metadata.setdefault("instruction_role", "developer")
    return {
        "backend": "local-llama",
        "upstream_model": name,
        "description": details.get("description", ""),
        "capabilities": capabilities,
        "features": features,
        "defaults": dict(details.get("defaults") or {}),
        "metadata": model_metadata,
    }


def _expand_legacy(raw: Any) -> dict[str, Any]:
    """Keep v0.2 profile documents and generated shorthand readable."""

    if not isinstance(raw, dict):
        raise RuntimeError("profile configuration must be a mapping")
    if {"version", "backends", "profiles"}.issubset(raw) and raw.get("version") == 1:
        backends = {
            name: {"kind": "llama.cpp", "description": value.get("description", "")}
            for name, value in raw.get("backends", {}).items()
        }
        models = {
            name: _legacy_model_spec(name, value) for name, value in raw.get("backends", {}).items()
        }
        backends.setdefault(
            "local-llama",
            {
                "kind": "llama.cpp",
                "base_url": "http://llama:8080",
                "coordinator": "explicit",
                "serialize_requests": True,
                "options": {"legacy": True},
            },
        )
        profiles = raw.get("profiles", {})
        return {"version": 2, "backends": backends, "models": models, "profiles": profiles}

    model_metadata: dict[str, Any] = {}
    profiles_raw = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else raw
    if isinstance(raw.get("models"), dict):
        model_metadata = raw["models"]

    backends: dict[str, dict[str, Any]] = {
        "local-llama": {
            "kind": "llama.cpp",
            "base_url": "http://llama:8080",
            "coordinator": "explicit",
            "serialize_requests": True,
        }
    }
    models: dict[str, dict[str, Any]] = {}
    profiles: dict[str, dict[str, Any]] = {}
    for profile_id, value in profiles_raw.items():
        if not isinstance(value, dict):
            raise RuntimeError(f"profile {profile_id} must be a mapping")
        backend_model = value.get("backend_model") or value.get("model")
        profile: dict[str, Any] = {
            "advertised": bool(value.get("advertised", True)),
            "description": str(value.get("description", profile_id)),
        }
        if value.get("route") == "auto":
            profile["route"] = "auto"
        elif isinstance(backend_model, str) and backend_model:
            profile["model"] = backend_model
            metadata = model_metadata.get(backend_model, {})
            capabilities = metadata.get("capabilities") if isinstance(metadata, dict) else None
            legacy_spec = _legacy_model_spec(backend_model, metadata)
            if capabilities:
                legacy_spec["capabilities"] = capabilities
            models.setdefault(backend_model, legacy_spec)
        else:
            raise RuntimeError(f"profile {profile_id} does not select a model")

        if isinstance(value.get("reasoning_effort"), str):
            profile["reasoning_effort"] = value["reasoning_effort"]
        if isinstance(value.get("system_prompt"), str):
            profile["system_prompt"] = value["system_prompt"]
        defaults = dict(value.get("defaults") or {})
        defaults.update(
            {key: item for key, item in value.items() if key not in _SHORTHAND_RESERVED_KEYS}
        )
        profile["defaults"] = defaults
        if isinstance(value.get("memory"), dict):
            profile["memory"] = value["memory"]
        profiles[str(profile_id)] = profile
    return {"version": 2, "backends": backends, "models": models, "profiles": profiles}


def load_profiles(path: Path) -> ProfileDocument:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"catalog configuration does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"invalid catalog YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("catalog configuration must be a mapping")
    expanded = _expand_v2(raw) if raw.get("version") == 2 else _expand_legacy(raw)
    return ProfileDocument.model_validate(expanded)
