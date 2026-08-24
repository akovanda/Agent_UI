from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx

from .memory import MemoryRecord, MemoryStore
from .memory_config import MemoryProviderConfig


class MemoryProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MemoryScope:
    namespace: str
    workspace_id: str
    context_id: str | None
    subject_id: str | None
    session_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "namespace": self.namespace,
            "workspace_id": self.workspace_id,
            "context_id": self.context_id,
            "subject_id": self.subject_id,
            "session_id": self.session_id,
        }


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    importance: float = 0.5
    created_at: datetime | None = None
    updated_at: datetime | None = None
    forgotten: bool = False
    rank: float = 0.0


class MemoryProvider(Protocol):
    kind: str
    enabled: bool
    capabilities: frozenset[str]

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def ping(self) -> bool: ...

    async def context_load(
        self, scope: MemoryScope, *, query: str, limit: int
    ) -> list[ProviderRecord]: ...

    async def ingest(
        self,
        scope: MemoryScope,
        *,
        content: str,
        source: str | None,
        metadata: dict[str, Any],
        importance: float,
        record_id: str | None = None,
    ) -> ProviderRecord: ...

    async def list_records(
        self, scope: MemoryScope, *, include_forgotten: bool = False
    ) -> list[ProviderRecord]: ...

    async def correct(
        self, scope: MemoryScope, record_id: str, *, content: str, reason: str
    ) -> ProviderRecord: ...

    async def forget(self, scope: MemoryScope, record_id: str, *, reason: str) -> bool: ...

    async def purge(self, scope: MemoryScope, record_id: str, *, reason: str) -> bool: ...

    async def export(self, scope: MemoryScope) -> list[dict[str, Any]]: ...


def _from_builtin(record: MemoryRecord) -> ProviderRecord:
    return ProviderRecord(
        id=str(record.id),
        content=record.content,
        metadata=record.metadata,
        source=record.source,
        importance=record.importance,
        created_at=record.created_at,
        updated_at=record.updated_at,
        forgotten=record.forgotten_at is not None,
        rank=record.rank,
    )


class BuiltinPostgresProvider:
    kind = "builtin-postgres"
    capabilities = frozenset(
        {"health", "context-load", "ingest", "list", "correct", "forget", "purge", "export"}
    )

    def __init__(self, store: MemoryStore):
        self.store = store
        self.enabled = store.enabled

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def ping(self) -> bool:
        return self.enabled and await self.store.ping()

    async def context_load(
        self, scope: MemoryScope, *, query: str, limit: int
    ) -> list[ProviderRecord]:
        records = await self.store.search(
            user_id=scope.subject_id or "anonymous",
            namespaces=[scope.workspace_id],
            query=query,
            limit=limit,
        )
        return [_from_builtin(item) for item in records]

    async def ingest(
        self,
        scope: MemoryScope,
        *,
        content: str,
        source: str | None,
        metadata: dict[str, Any],
        importance: float,
        record_id: str | None = None,
    ) -> ProviderRecord:
        stable_id = None
        if record_id:
            stable_id = uuid5(
                NAMESPACE_URL,
                json.dumps(
                    {
                        "provider": self.kind,
                        "namespace": scope.namespace,
                        "workspace_id": scope.workspace_id,
                        "context_id": scope.context_id,
                        "subject_id": scope.subject_id,
                        "record_id": record_id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        record = await self.store.add(
            user_id=scope.subject_id or "anonymous",
            namespace=scope.workspace_id,
            content=content,
            source=source,
            metadata={**metadata, "scope": scope.as_dict()},
            importance=importance,
            record_id=stable_id,
        )
        return _from_builtin(record)

    async def list_records(
        self, scope: MemoryScope, *, include_forgotten: bool = False
    ) -> list[ProviderRecord]:
        method = getattr(self.store, "list_records", None)
        if method is None:
            return []
        records = await method(
            user_id=scope.subject_id or "anonymous",
            namespace=scope.workspace_id,
            include_forgotten=include_forgotten,
        )
        return [_from_builtin(item) for item in records]

    async def correct(
        self, scope: MemoryScope, record_id: str, *, content: str, reason: str
    ) -> ProviderRecord:
        del reason
        try:
            parsed = UUID(record_id)
        except ValueError as exc:
            raise MemoryProviderError("invalid built-in record id") from exc
        method = getattr(self.store, "correct", None)
        if method is None:
            raise MemoryProviderError("provider does not support correction")
        return _from_builtin(
            await method(
                user_id=scope.subject_id or "anonymous",
                namespace=scope.workspace_id,
                record_id=parsed,
                content=content,
            )
        )

    async def forget(self, scope: MemoryScope, record_id: str, *, reason: str) -> bool:
        del reason
        method = getattr(self.store, "forget", None)
        if method is None:
            raise MemoryProviderError("provider does not support forgetting")
        return bool(
            await method(
                user_id=scope.subject_id or "anonymous",
                namespace=scope.workspace_id,
                record_id=UUID(record_id),
            )
        )

    async def purge(self, scope: MemoryScope, record_id: str, *, reason: str) -> bool:
        del reason
        method = getattr(self.store, "purge", None)
        if method is None:
            raise MemoryProviderError("provider does not support purging")
        return bool(
            await method(
                user_id=scope.subject_id or "anonymous",
                namespace=scope.workspace_id,
                record_id=UUID(record_id),
            )
        )

    async def export(self, scope: MemoryScope) -> list[dict[str, Any]]:
        return [
            {
                "record_id": record.id,
                "content": record.content,
                "source": record.source,
                "importance": record.importance,
                "metadata": record.metadata,
                "forgotten": record.forgotten,
            }
            for record in await self.list_records(scope, include_forgotten=True)
        ]

    async def migrate_legacy(self, principal_id: str, scope: MemoryScope) -> int:
        method = getattr(self.store, "migrate_legacy", None)
        if method is None:
            return 0
        return int(
            await method(
                user_id=principal_id,
                target_user_id=scope.subject_id or "anonymous",
                target_namespace=scope.workspace_id,
                namespaces=["user", "general", "projects", "code", "infrastructure"],
            )
        )


class DisabledMemoryProvider:
    kind = "disabled"
    enabled = False
    capabilities = frozenset()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def ping(self) -> bool:
        return True

    async def context_load(self, *_: Any, **__: Any) -> list[ProviderRecord]:
        return []

    async def ingest(self, *_: Any, **__: Any) -> ProviderRecord:
        raise MemoryProviderError("memory provider is disabled")

    async def list_records(self, *_: Any, **__: Any) -> list[ProviderRecord]:
        return []

    async def correct(self, *_: Any, **__: Any) -> ProviderRecord:
        raise MemoryProviderError("memory provider is disabled")

    async def forget(self, *_: Any, **__: Any) -> bool:
        return False

    async def purge(self, *_: Any, **__: Any) -> bool:
        return False

    async def export(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
        return []


class ContinuityHttpProvider:
    kind = "continuity-http"
    enabled = True
    _required_capabilities = frozenset(
        {"health", "context-load", "ingest", "list", "correct", "forget", "purge", "export"}
    )

    def __init__(
        self,
        config: MemoryProviderConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=(config.base_url or "").rstrip("/"),
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=config.timeout_seconds,
            transport=transport,
        )
        self.capabilities: frozenset[str] = frozenset()
        self.start_error: str | None = None

    async def start(self) -> None:
        try:
            response = await self.client.get("/v1/info")
            response.raise_for_status()
            info = response.json()
            if info.get("contract") != "continuity-http/1":
                raise MemoryProviderError("provider does not implement continuity-http/1")
            capabilities = info.get("capabilities", [])
            if isinstance(capabilities, dict):
                capabilities = [
                    name for name, supported in capabilities.items() if supported is not False
                ]
            normalized = {str(item).replace("_", "-") for item in capabilities}
            if "hard-purge" in normalized:
                normalized.add("purge")
            self.capabilities = frozenset(normalized)
            missing = self._required_capabilities - self.capabilities
            if missing:
                raise MemoryProviderError(
                    f"continuity provider lacks required capabilities: {sorted(missing)}"
                )
            self.start_error = None
        except Exception as exc:
            self.start_error = str(exc)
            raise

    async def close(self) -> None:
        await self.client.aclose()

    async def ping(self) -> bool:
        if self.start_error is not None:
            try:
                await self.start()
            except Exception:
                return False
        try:
            response = await self.client.get("/healthz")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.post(path, json=body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MemoryProviderError(f"continuity request failed: {exc}") from exc
        value = response.json()
        if not isinstance(value, dict):
            raise MemoryProviderError("continuity provider returned a non-object response")
        return value

    @staticmethod
    def _record(value: dict[str, Any]) -> ProviderRecord:
        wrapper = value
        payload = value.get("payload")
        if isinstance(payload, dict):
            value = {
                **payload,
                "rank": wrapper.get("rank", wrapper.get("score", payload.get("rank"))),
                "scope_key": wrapper.get("scope_key", payload.get("scope_key")),
            }
        content = value.get("content")
        if not isinstance(content, str):
            if "value" in value:
                content = json.dumps(value["value"], ensure_ascii=False)
            else:
                content = str(value.get("title") or value.get("summary") or "")
        return ProviderRecord(
            id=str(value.get("record_id") or value.get("id") or uuid4()),
            content=content,
            metadata={
                **dict(value.get("metadata") or {}),
                **({"provider_scope_key": value["scope_key"]} if value.get("scope_key") else {}),
            },
            source=(value.get("origin") or {}).get("system")
            if isinstance(value.get("origin"), dict)
            else None,
            forgotten=bool(value.get("forgotten") or value.get("forgotten_at")),
            rank=float(value.get("rank") or value.get("score") or 0.0),
        )

    async def context_load(
        self, scope: MemoryScope, *, query: str, limit: int
    ) -> list[ProviderRecord]:
        result = await self._post(
            "/v1/context/load",
            {
                "scope": scope.as_dict(),
                "query": query,
                "summary_limit": limit,
                "evidence_limit_per_summary": limit,
                "record_limit": limit,
            },
        )
        items: list[dict[str, Any]] = []
        for key in (
            "record_hits",
            "active_facts",
            "open_commitments",
            "summary_hits",
            "evidence_hits",
        ):
            value = result.get(key, [])
            if isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict))
        records: list[ProviderRecord] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            record = self._record(item)
            identity = (record.id, record.content)
            if identity in seen or not record.content:
                continue
            seen.add(identity)
            records.append(record)
            if len(records) >= limit:
                break
        return records

    async def ingest(
        self,
        scope: MemoryScope,
        *,
        content: str,
        source: str | None,
        metadata: dict[str, Any],
        importance: float,
        record_id: str | None = None,
    ) -> ProviderRecord:
        identifier = record_id or str(uuid4())
        await self._post(
            "/v1/records",
            {
                "scope": scope.as_dict(),
                "record_id": identifier,
                "record_kind": "note",
                "content": content,
                "origin": {"system": source or "agent-ui", "kind": "memory"},
                "metadata": {**metadata, "importance": importance},
            },
        )
        return ProviderRecord(
            id=identifier,
            content=content,
            source=source,
            metadata=metadata,
            importance=importance,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def list_records(
        self, scope: MemoryScope, *, include_forgotten: bool = False
    ) -> list[ProviderRecord]:
        result = await self._post(
            "/v1/records/list",
            {"scope": scope.as_dict(), "include_forgotten": include_forgotten, "limit": 1000},
        )
        values = result.get("items", result.get("records", []))
        return [self._record(item) for item in values if isinstance(item, dict)]

    async def correct(
        self, scope: MemoryScope, record_id: str, *, content: str, reason: str
    ) -> ProviderRecord:
        result = await self._post(
            "/v1/records/correct",
            {
                "scope": scope.as_dict(),
                "record_id": record_id,
                "content": content,
                "reason": reason,
            },
        )
        value = result.get("record", result)
        if isinstance(value, dict) and value.get("content") is not None:
            return self._record(value)
        return ProviderRecord(id=record_id, content=content)

    async def forget(self, scope: MemoryScope, record_id: str, *, reason: str) -> bool:
        result = await self._post(
            "/v1/records/forget",
            {"scope": scope.as_dict(), "record_id": record_id, "reason": reason},
        )
        return result.get("status") in {"ok", "forgotten"}

    async def purge(self, scope: MemoryScope, record_id: str, *, reason: str) -> bool:
        result = await self._post(
            "/v1/records/purge",
            {"scope": scope.as_dict(), "record_id": record_id, "reason": reason},
        )
        return result.get("status") in {"ok", "purged"}

    async def export(self, scope: MemoryScope) -> list[dict[str, Any]]:
        result = await self._post(
            "/v1/records/export", {"scope": scope.as_dict(), "include_forgotten": True}
        )
        values = result.get("items", result.get("records", []))
        return [dict(item) for item in values if isinstance(item, dict)]
