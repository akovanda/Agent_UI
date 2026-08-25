from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class MemoryProviderConfig(BaseModel):
    """A provider declaration with secrets referenced through the environment."""

    kind: Literal["builtin-postgres", "continuity-http", "disabled"] = "builtin-postgres"
    base_url: str | None = None
    token_env: str = "CONTINUITY_API_TOKEN"
    namespace: str = "personal"
    required: bool = False
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)

    @model_validator(mode="after")
    def validate_provider(self) -> MemoryProviderConfig:
        if self.kind == "continuity-http" and not self.base_url:
            raise ValueError("continuity-http providers require base_url")
        return self

    @property
    def token(self) -> str:
        return os.getenv(self.token_env, "")


class AutomaticMemoryConfig(BaseModel):
    enabled: bool = False
    default_user_enabled: bool = True
    capture: bool = True
    capture_mode: Literal["review", "automatic"] = "review"
    retrieval: bool = True
    capabilities: list[str] = Field(default_factory=lambda: ["chat", "code", "agent"])
    excluded_experiences: list[str] = Field(default_factory=lambda: ["story", "storyteller"])
    extractor_experience: str = "chat"
    max_candidates: int = Field(default=3, ge=1, le=10)
    queue_size: int = Field(default=128, ge=1, le=10000)


class PersonalMemoryConfig(BaseModel):
    context: str = "assistant"
    subject_hmac_env: str = "MEMORY_SUBJECT_HMAC_KEY"


class RetentionConfig(BaseModel):
    pending_days: int = Field(default=30, ge=1, le=365)


class BridgePolicy(BaseModel):
    source_kind: Literal["personal", "game"]
    target_kind: Literal["personal", "game"]


class BridgeConfig(BaseModel):
    enabled: bool = False
    operator_allowlist: list[BridgePolicy] = Field(default_factory=list)


class IdentityConfig(BaseModel):
    forwarded_jwt_header: str = "X-OpenWebUI-User-Jwt"
    forwarded_jwt_secret_env: str = "WEBUI_SECRET_KEY"
    browser_cookie_name: str = "token"
    browser_cookie_secret_env: str = "WEBUI_SECRET_KEY"
    allow_legacy_loopback_headers: bool = False


class MemoryConfig(BaseModel):
    version: Literal[1] = 1
    provider: MemoryProviderConfig = Field(default_factory=MemoryProviderConfig)
    automatic: AutomaticMemoryConfig = Field(default_factory=AutomaticMemoryConfig)
    personal: PersonalMemoryConfig = Field(default_factory=PersonalMemoryConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    bridges: BridgeConfig = Field(default_factory=BridgeConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)


def _read_yaml(path: Path, *, required: bool) -> dict:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"memory configuration does not exist: {path}")
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"memory configuration must be a mapping: {path}")
    return loaded


def _merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        elif value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def load_memory_config(path: Path, overlay_path: Path | None = None) -> MemoryConfig:
    raw = _read_yaml(path, required=True)
    if overlay_path is not None:
        raw = _merge(raw, _read_yaml(overlay_path, required=False))
    return MemoryConfig.model_validate(raw)
