from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .config import Endpoint


class MemoryCreate(BaseModel):
    user_id: str | None = Field(default=None, min_length=1, max_length=200)
    namespace: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=50000)
    source: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
    importance: float = Field(default=0.5, ge=0, le=1)


class RoutePreview(BaseModel):
    model: str = "auto"
    endpoint: Endpoint = "chat"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    profile_override: str | None = None
