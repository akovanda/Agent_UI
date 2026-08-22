from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

import local_ai_hub.memory as memory_module
from local_ai_hub.memory import (
    MemoryRecord,
    NullMemoryStore,
    PostgresMemoryStore,
    render_memory_context,
)


@pytest.mark.asyncio
async def test_null_store_is_safe_and_disabled() -> None:
    store = NullMemoryStore()
    await store.start()
    assert await store.ping()
    assert await store.search(query="x") == []
    with pytest.raises(RuntimeError, match="disabled"):
        await store.add(content="x")
    await store.close()


def test_memory_context_is_bounded_and_marks_data_untrusted() -> None:
    now = datetime.now(UTC)
    records = [
        MemoryRecord(
            id=uuid4(),
            user_id="andrew",
            namespace="general",
            content="A" * 500,
            source=None,
            metadata={},
            importance=0.5,
            created_at=now,
            updated_at=now,
        )
    ]
    text = render_memory_context(records, 220)
    assert text is not None
    assert len(text) <= 221
    assert "untrusted reference data" in text
    assert render_memory_context([], 100) is None
    assert render_memory_context(records, 0) is None


@pytest.mark.asyncio
async def test_postgres_store_reports_missing_optional_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_module, "asyncpg", None)
    store = PostgresMemoryStore("postgresql://unused")
    with pytest.raises(RuntimeError, match="asyncpg"):
        await store.start()
    assert not await store.ping()
    await store.close()


class FakePool:
    def __init__(self, row):
        self.row = row
        self.fetch_args = None

    async def fetchrow(self, *_args):
        return self.row

    async def fetch(self, *_args):
        self.fetch_args = _args
        return [self.row]


@pytest.mark.asyncio
async def test_postgres_add_and_search_mapping_without_database() -> None:
    now = datetime.now(UTC)
    row = {
        "id": uuid4(),
        "user_id": "andrew",
        "namespace": "projects",
        "content": "Local AI Hub",
        "source": "test",
        "metadata": {"kind": "fact"},
        "importance": 0.9,
        "created_at": now,
        "updated_at": now,
        "rank": 0.5,
    }
    store = PostgresMemoryStore("postgresql://unused")
    store.pool = FakePool(row)
    added = await store.add(
        user_id="andrew",
        namespace="projects",
        content="Local AI Hub",
        source="test",
        metadata={"kind": "fact"},
        importance=0.9,
    )
    found = await store.search(
        user_id="andrew", namespaces=["projects"], query="local hub", limit=3
    )
    assert added.content == "Local AI Hub"
    assert found[0].rank == 0.5
    assert await store.search(user_id="andrew", namespaces=[], query="x", limit=3) == []
