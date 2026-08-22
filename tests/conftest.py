from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from local_ai_hub.config import Settings
from local_ai_hub.memory import MemoryRecord


class FakeMemoryStore:
    enabled = True

    def __init__(self, records: list[MemoryRecord] | None = None):
        self.records = records or []
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def ping(self) -> bool:
        return True

    async def add(
        self,
        *,
        user_id: str,
        namespace: str,
        content: str,
        source: str | None,
        metadata: dict[str, Any],
        importance: float,
    ) -> MemoryRecord:
        now = datetime.now(UTC)
        record = MemoryRecord(
            id=uuid4(),
            user_id=user_id,
            namespace=namespace,
            content=content,
            source=source,
            metadata=metadata,
            importance=importance,
            created_at=now,
            updated_at=now,
        )
        self.records.append(record)
        return record

    async def search(
        self,
        *,
        user_id: str,
        namespaces: list[str],
        query: str,
        limit: int,
    ) -> list[MemoryRecord]:
        return [
            record
            for record in self.records
            if record.user_id == user_id and record.namespace in namespaces
        ][:limit]


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "gateway_api_key": "test-secret-key",
        "profile_config_path": Path("config/gateway/profiles.yaml"),
        "model_coordinator_mode": "none",
        "memory_enabled": True,
        "database_url": None,
    }
    values.update(overrides)
    return Settings(**values)
