from __future__ import annotations

from dataclasses import replace
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
        record_id=None,
    ) -> MemoryRecord:
        if record_id is not None:
            existing = next((item for item in self.records if item.id == record_id), None)
            if existing is not None:
                if existing.user_id != user_id or existing.namespace != namespace:
                    raise RuntimeError("memory record id already belongs to another scope")
                return existing
        now = datetime.now(UTC)
        record = MemoryRecord(
            id=record_id or uuid4(),
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
            if record.user_id == user_id
            and record.namespace in namespaces
            and record.forgotten_at is None
        ][:limit]

    async def list_records(
        self, *, user_id: str, namespace: str, include_forgotten: bool = False
    ) -> list[MemoryRecord]:
        return [
            record
            for record in self.records
            if record.user_id == user_id
            and record.namespace == namespace
            and (include_forgotten or record.forgotten_at is None)
        ]

    async def correct(
        self, *, user_id: str, namespace: str, record_id, content: str
    ) -> MemoryRecord:
        for index, record in enumerate(self.records):
            if (
                record.id == record_id
                and record.user_id == user_id
                and record.namespace == namespace
            ):
                updated = replace(
                    record,
                    content=content,
                    forgotten_at=None,
                    updated_at=datetime.now(UTC),
                )
                self.records[index] = updated
                return updated
        raise KeyError(str(record_id))

    async def forget(self, *, user_id: str, namespace: str, record_id) -> bool:
        for index, record in enumerate(self.records):
            if (
                record.id == record_id
                and record.user_id == user_id
                and record.namespace == namespace
            ):
                self.records[index] = replace(record, forgotten_at=datetime.now(UTC))
                return True
        return False

    async def purge(self, *, user_id: str, namespace: str, record_id) -> bool:
        before = len(self.records)
        self.records = [
            record
            for record in self.records
            if not (
                record.id == record_id
                and record.user_id == user_id
                and record.namespace == namespace
            )
        ]
        return len(self.records) != before

    async def migrate_legacy(
        self,
        *,
        user_id: str,
        target_user_id: str,
        target_namespace: str,
        namespaces: list[str],
    ) -> int:
        migrated = 0
        for index, record in enumerate(self.records):
            if record.user_id == user_id and record.namespace in namespaces:
                self.records[index] = replace(
                    record,
                    user_id=target_user_id,
                    namespace=target_namespace,
                    metadata={**record.metadata, "legacy_namespace": record.namespace},
                )
                migrated += 1
        return migrated


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "gateway_api_key": "test-secret-key",
        "profile_config_path": Path("tests/fixtures/legacy-profiles.yaml"),
        "model_coordinator_mode": "none",
        "memory_enabled": True,
        "memory_config_path": Path("tests/fixtures/memory-enabled.yaml"),
        "database_url": None,
    }
    values.update(overrides)
    return Settings(**values)
