from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Endpoint = Literal["chat", "completion", "image", "embedding", "rerank"]
ProviderType = Literal["llama_cpp", "openai_compatible"]
ReasoningTransport = Literal["none", "flat", "object", "chat_template"]


class Settings(BaseSettings):
    """Process configuration sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str | None = None
    gateway_api_key: SecretStr = SecretStr("local-development-key")
    default_user_id: str = "local-user"
    registry_config_path: Path = Field(
        default=Path("config/registry.yaml"),
        validation_alias=AliasChoices("REGISTRY_CONFIG_PATH", "PROFILE_CONFIG_PATH"),
    )
    model_coordinator_mode: Literal["explicit", "autoload", "none"] = "explicit"
    model_load_timeout_seconds: float = 360.0
    model_poll_interval_seconds: float = 1.0
    upstream_connect_timeout_seconds: float = 10.0
    upstream_write_timeout_seconds: float = 60.0
    memory_enabled: bool = True
    memory_required: bool = False
    memory_top_k: int = Field(default=6, ge=0, le=30)
    memory_max_chars: int = Field(default=6000, ge=0, le=50000)
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:8001,http://127.0.0.1:8001"
    )
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


class MemoryProfile(BaseModel):
    enabled: bool = False
    namespaces: list[str] = Field(default_factory=list)


class ReasoningSupport(BaseModel):
    transport: ReasoningTransport = "none"
    field: str = "reasoning_effort"
    levels: list[str] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)
    default: str | None = None


class ModelFeatures(BaseModel):
    developer_role: bool = False
    tool_calling: bool = False
    streaming: bool = True
    reasoning: ReasoningSupport = Field(default_factory=ReasoningSupport)


class Source(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["managed", "host", "pvc", "nfs", "csi", "host_path"]
    description: str = ""
    mount_path: str
    read_only: bool = True
    host_path: str | None = None
    kubernetes: dict[str, Any] = Field(default_factory=dict)


class Provider(BaseModel):
    type: ProviderType = "openai_compatible"
    enabled: bool = True
    required: bool = False
    description: str = ""
    base_url: str
    control_url: str | None = None
    api_key_env: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    endpoints: dict[str, str] = Field(default_factory=dict)
    health_path: str | None = "/health"
    resource_group: str | None = None
    max_concurrency: int = Field(default=1, ge=1, le=64)


class Artifact(BaseModel):
    source: str
    path: str

    @model_validator(mode="after")
    def validate_relative_path(self) -> Artifact:
        candidate = Path(self.path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("artifact path must be relative to its source mount")
        return self


class Model(BaseModel):
    provider: str
    display_name: str = ""
    description: str = ""
    upstream_model: str | None = None
    coordinator_model: str | None = None
    enabled: bool = True
    priority: int = 0
    capabilities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    artifact: Artifact | None = None
    runtime: dict[str, Any] = Field(default_factory=dict)
    features: ModelFeatures = Field(default_factory=ModelFeatures)


class CapabilitySelector(BaseModel):
    all_of: list[str] = Field(default_factory=list)
    any_of: list[str] = Field(default_factory=list)
    none_of: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    prefer_capabilities: list[str] = Field(default_factory=list)
    prefer_tags: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            (
                self.all_of,
                self.any_of,
                self.none_of,
                self.tags,
                self.prefer_capabilities,
                self.prefer_tags,
            )
        )


class Profile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    advertised: bool = True
    route: Literal["auto"] | None = None
    endpoint: Endpoint = "chat"
    model: str | None = None
    selector: CapabilitySelector = Field(default_factory=CapabilitySelector, alias="requires")
    fallback_selector: CapabilitySelector | None = Field(default=None, alias="fallback_requires")
    description: str = ""
    reasoning_effort: str | None = None
    system_prompt: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    memory: MemoryProfile = Field(default_factory=MemoryProfile)

    @model_validator(mode="after")
    def validate_target(self) -> Profile:
        if self.route == "auto":
            if self.model is not None:
                raise ValueError("automatic profiles cannot bind a model")
            if self.endpoint != "chat":
                raise ValueError("automatic routing is currently available only for chat")
        elif self.model is None and self.selector.is_empty():
            raise ValueError("concrete profiles require a model or capability selector")
        return self


class RouteRule(BaseModel):
    profile: str
    priority: int = 0
    prefixes: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)


class RegistryDocument(BaseModel):
    version: Literal[2]
    providers: dict[str, Provider] = Field(default_factory=dict)
    sources: dict[str, Source] = Field(default_factory=dict)
    models: dict[str, Model] = Field(default_factory=dict)
    profiles: dict[str, Profile] = Field(default_factory=dict)
    routes: list[RouteRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> RegistryDocument:
        unknown_providers = {
            model.provider for model in self.models.values() if model.provider not in self.providers
        }
        if unknown_providers:
            raise ValueError(f"models reference unknown providers: {sorted(unknown_providers)}")
        unknown_sources = {
            model.artifact.source
            for model in self.models.values()
            if model.artifact and model.artifact.source not in self.sources
        }
        if unknown_sources:
            raise ValueError(f"models reference unknown sources: {sorted(unknown_sources)}")
        unknown_models = {
            profile.model
            for profile in self.profiles.values()
            if profile.model and profile.model not in self.models
        }
        if unknown_models:
            raise ValueError(f"profiles reference unknown models: {sorted(unknown_models)}")
        unknown_route_profiles = {
            rule.profile for rule in self.routes if rule.profile not in self.profiles
        }
        if unknown_route_profiles:
            raise ValueError(
                f"routes reference unknown profiles: {sorted(unknown_route_profiles)}"
            )
        return self


def load_registry(path: Path) -> RegistryDocument:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"registry configuration does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"invalid registry YAML in {path}: {exc}") from exc
    try:
        return RegistryDocument.model_validate(raw)
    except Exception as exc:
        raise RuntimeError(f"invalid registry configuration in {path}: {exc}") from exc


# Compatibility for integrations that still import the pre-0.3 function name.
load_profiles = load_registry
ProfileDocument = RegistryDocument
Backend = Model
