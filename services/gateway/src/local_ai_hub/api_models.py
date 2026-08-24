from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryCreate(BaseModel):
    user_id: str | None = Field(default=None, min_length=1, max_length=200)
    namespace: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=50000)
    source: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
    importance: float = Field(default=0.5, ge=0, le=1)


class RoutePreview(BaseModel):
    model: str = "auto"
    messages: list[dict[str, Any]]
    profile_override: str | None = None


class MemorySettingsUpdate(BaseModel):
    enabled: bool
    capture_enabled: bool
    retrieval_enabled: bool


class ProposalApproval(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=50000)


class MemoryReason(BaseModel):
    reason: str = Field(default="", max_length=1000)


class MemoryCorrection(MemoryReason):
    content: str = Field(min_length=1, max_length=50000)


class MemoryImport(BaseModel):
    format: str = "agent-ui-memory/1"
    records: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)


class BridgeConsentUpdate(BaseModel):
    source_space_id: str
    target_space_id: str
    source_consented: bool
    target_consented: bool


class GameSpaceCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    namespace: str = Field(default="game", min_length=1, max_length=100)
    world_id: str = Field(min_length=1, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=200)
    player_id: str | None = Field(default=None, max_length=200)


class GameEventCreate(BaseModel):
    external_id: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=50000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = Field(default=None, max_length=500)


class GameContextRequest(BaseModel):
    space_id: str
    query: str = Field(default="", max_length=2000)
    limit: int = Field(default=10, ge=1, le=100)
    session_id: str | None = Field(default=None, max_length=500)
