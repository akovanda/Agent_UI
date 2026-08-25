from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Any, Protocol
from uuid import UUID, uuid4

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class UserMemorySettings:
    enabled: bool
    capture_enabled: bool
    retrieval_enabled: bool


@dataclass(frozen=True, slots=True)
class MemorySpace:
    id: UUID
    kind: str
    owner_principal_id: str
    display_name: str
    provider_namespace: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryProposal:
    id: UUID
    space_id: UUID
    principal_id: str
    content: str | None
    state: str
    source_experience: str
    source_chat_hash: str | None
    metadata: dict[str, Any]
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryRecordRef:
    id: UUID
    space_id: UUID
    principal_id: str
    provider_record_id: str
    proposal_id: UUID | None
    external_id: str | None
    status: str
    provenance: dict[str, Any]


class MemoryRepository(Protocol):
    persistent: bool

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def ping(self) -> bool: ...

    async def get_settings(self, principal_id: str) -> UserMemorySettings | None: ...

    async def set_settings(
        self, principal_id: str, settings: UserMemorySettings
    ) -> UserMemorySettings: ...

    async def ensure_personal_space(
        self, principal_id: str, provider_namespace: str
    ) -> MemorySpace: ...

    async def create_game_space(
        self,
        principal_id: str,
        display_name: str,
        provider_namespace: str,
        metadata: dict[str, Any],
    ) -> MemorySpace: ...

    async def get_space(self, principal_id: str, space_id: UUID) -> MemorySpace | None: ...

    async def list_spaces(self, principal_id: str) -> list[MemorySpace]: ...

    async def add_proposal(
        self,
        *,
        space_id: UUID,
        principal_id: str,
        content: str,
        source_experience: str,
        source_chat_hash: str | None,
        metadata: dict[str, Any],
        pending_days: int,
    ) -> MemoryProposal: ...

    async def list_proposals(
        self, principal_id: str, state: str = "pending"
    ) -> list[MemoryProposal]: ...

    async def get_proposal(self, principal_id: str, proposal_id: UUID) -> MemoryProposal | None: ...

    async def transition_proposal(
        self, principal_id: str, proposal_id: UUID, state: str, content: str | None
    ) -> MemoryProposal | None: ...

    async def expire_proposals(self) -> int: ...

    async def add_record_ref(
        self,
        *,
        space_id: UUID,
        principal_id: str,
        provider_record_id: str,
        proposal_id: UUID | None,
        external_id: str | None,
        provenance: dict[str, Any],
    ) -> MemoryRecordRef: ...

    async def get_record_ref(
        self, principal_id: str, reference_id: UUID
    ) -> MemoryRecordRef | None: ...

    async def find_external_ref(
        self, principal_id: str, space_id: UUID, external_id: str
    ) -> MemoryRecordRef | None: ...

    async def list_record_refs(
        self, principal_id: str, space_id: UUID
    ) -> list[MemoryRecordRef]: ...

    async def set_record_status(
        self, principal_id: str, reference_id: UUID, status: str
    ) -> None: ...

    async def purge_record_content(self, principal_id: str, reference_id: UUID) -> None: ...

    async def set_bridge_consent(
        self,
        principal_id: str,
        source_space_id: UUID,
        target_space_id: UUID,
        *,
        source_consented: bool,
        target_consented: bool,
    ) -> dict[str, Any]: ...

    async def active_bridges(
        self, principal_id: str, target_space_id: UUID
    ) -> list[MemorySpace]: ...

    async def audit(
        self,
        principal_id: str,
        action: str,
        target_type: str,
        target_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value or {})


def _space(row: Any) -> MemorySpace:
    return MemorySpace(
        id=row["id"],
        kind=row["kind"],
        owner_principal_id=row["owner_principal_id"],
        display_name=row["display_name"],
        provider_namespace=row["provider_namespace"],
        metadata=_metadata(row["metadata"]),
    )


def _proposal(row: Any) -> MemoryProposal:
    return MemoryProposal(
        id=row["id"],
        space_id=row["space_id"],
        principal_id=row["principal_id"],
        content=row["content"],
        state=row["state"],
        source_experience=row["source_experience"],
        source_chat_hash=row["source_chat_hash"],
        metadata=_metadata(row["metadata"]),
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


def _reference(row: Any) -> MemoryRecordRef:
    return MemoryRecordRef(
        id=row["id"],
        space_id=row["space_id"],
        principal_id=row["principal_id"],
        provider_record_id=row["provider_record_id"],
        proposal_id=row["proposal_id"],
        external_id=row["external_id"],
        status=row["status"],
        provenance=_metadata(row["provenance"]),
    )


def _audit_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    blocked = {"content", "text", "prompt", "message", "messages"}
    return {key: value for key, value in (metadata or {}).items() if key.lower() not in blocked}


class PostgresMemoryRepository:
    persistent = True

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Any | None = None

    async def start(self) -> None:
        if asyncpg is None:
            raise RuntimeError("asyncpg is required for persistent memory governance")
        self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
        async with self.pool.acquire() as connection:
            migration = files("local_ai_hub").joinpath("sql", "002_memory_foundation.sql")
            await connection.execute(migration.read_text(encoding="utf-8"))

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def ping(self) -> bool:
        if self.pool is None:
            return False
        try:
            return bool(await self.pool.fetchval("SELECT true"))
        except Exception:
            return False

    def _pool(self) -> Any:
        if self.pool is None:
            raise RuntimeError("memory repository has not been started")
        return self.pool

    async def get_settings(self, principal_id: str) -> UserMemorySettings | None:
        row = await self._pool().fetchrow(
            "SELECT enabled, capture_enabled, retrieval_enabled FROM memory_user_settings "
            "WHERE principal_id = $1",
            principal_id,
        )
        return UserMemorySettings(**dict(row)) if row else None

    async def set_settings(
        self, principal_id: str, settings: UserMemorySettings
    ) -> UserMemorySettings:
        row = await self._pool().fetchrow(
            """
            INSERT INTO memory_user_settings
                (principal_id, enabled, capture_enabled, retrieval_enabled)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (principal_id) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                capture_enabled = EXCLUDED.capture_enabled,
                retrieval_enabled = EXCLUDED.retrieval_enabled,
                updated_at = now()
            RETURNING enabled, capture_enabled, retrieval_enabled
            """,
            principal_id,
            settings.enabled,
            settings.capture_enabled,
            settings.retrieval_enabled,
        )
        return UserMemorySettings(**dict(row))

    async def ensure_personal_space(
        self, principal_id: str, provider_namespace: str
    ) -> MemorySpace:
        async with self._pool().acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO memory_spaces
                    (kind, owner_principal_id, display_name, provider_namespace)
                VALUES ('personal', $1, 'Personal', $2)
                ON CONFLICT (kind, owner_principal_id, display_name) DO UPDATE SET
                    provider_namespace = EXCLUDED.provider_namespace
                RETURNING *
                """,
                principal_id,
                provider_namespace,
            )
            await connection.execute(
                """
                INSERT INTO memory_space_memberships (space_id, principal_id, role)
                VALUES ($1, $2, 'owner')
                ON CONFLICT (space_id, principal_id) DO UPDATE SET role = 'owner'
                """,
                row["id"],
                principal_id,
            )
        return _space(row)

    async def create_game_space(
        self,
        principal_id: str,
        display_name: str,
        provider_namespace: str,
        metadata: dict[str, Any],
    ) -> MemorySpace:
        async with self._pool().acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO memory_spaces
                    (kind, owner_principal_id, display_name, provider_namespace, metadata)
                VALUES ('game', $1, $2, $3, $4::jsonb)
                ON CONFLICT (kind, owner_principal_id, display_name) DO UPDATE SET
                    provider_namespace = EXCLUDED.provider_namespace,
                    metadata = memory_spaces.metadata || EXCLUDED.metadata
                RETURNING *
                """,
                principal_id,
                display_name,
                provider_namespace,
                json.dumps(metadata),
            )
            await connection.execute(
                """
                INSERT INTO memory_space_memberships (space_id, principal_id, role)
                VALUES ($1, $2, 'owner')
                ON CONFLICT (space_id, principal_id) DO UPDATE SET role = 'owner'
                """,
                row["id"],
                principal_id,
            )
        return _space(row)

    async def get_space(self, principal_id: str, space_id: UUID) -> MemorySpace | None:
        row = await self._pool().fetchrow(
            """
            SELECT s.* FROM memory_spaces s
            JOIN memory_space_memberships m ON m.space_id = s.id
            WHERE s.id = $1 AND m.principal_id = $2
            """,
            space_id,
            principal_id,
        )
        return _space(row) if row else None

    async def list_spaces(self, principal_id: str) -> list[MemorySpace]:
        rows = await self._pool().fetch(
            """
            SELECT s.* FROM memory_spaces s
            JOIN memory_space_memberships m ON m.space_id = s.id
            WHERE m.principal_id = $1 ORDER BY s.kind, s.display_name
            """,
            principal_id,
        )
        return [_space(row) for row in rows]

    async def add_proposal(
        self,
        *,
        space_id: UUID,
        principal_id: str,
        content: str,
        source_experience: str,
        source_chat_hash: str | None,
        metadata: dict[str, Any],
        pending_days: int,
    ) -> MemoryProposal:
        row = await self._pool().fetchrow(
            """
            INSERT INTO memory_proposals
                (space_id, principal_id, content, state, source_experience,
                 source_chat_hash, metadata, expires_at)
            VALUES ($1, $2, $3, 'pending', $4, $5, $6::jsonb, now() + ($7 * interval '1 day'))
            RETURNING *
            """,
            space_id,
            principal_id,
            content,
            source_experience,
            source_chat_hash,
            json.dumps(metadata),
            pending_days,
        )
        return _proposal(row)

    async def list_proposals(
        self, principal_id: str, state: str = "pending"
    ) -> list[MemoryProposal]:
        rows = await self._pool().fetch(
            "SELECT * FROM memory_proposals WHERE principal_id = $1 AND state = $2 "
            "ORDER BY created_at DESC",
            principal_id,
            state,
        )
        return [_proposal(row) for row in rows]

    async def get_proposal(self, principal_id: str, proposal_id: UUID) -> MemoryProposal | None:
        row = await self._pool().fetchrow(
            "SELECT * FROM memory_proposals WHERE id = $1 AND principal_id = $2",
            proposal_id,
            principal_id,
        )
        return _proposal(row) if row else None

    async def transition_proposal(
        self, principal_id: str, proposal_id: UUID, state: str, content: str | None
    ) -> MemoryProposal | None:
        stored_content = None if state in {"rejected", "expired"} else content
        row = await self._pool().fetchrow(
            """
            UPDATE memory_proposals
            SET state = $3, content = $4, updated_at = now()
            WHERE id = $1 AND principal_id = $2 AND state = 'pending'
            RETURNING *
            """,
            proposal_id,
            principal_id,
            state,
            stored_content,
        )
        return _proposal(row) if row else None

    async def expire_proposals(self) -> int:
        result = await self._pool().execute(
            """
            UPDATE memory_proposals SET state = 'expired', content = NULL, updated_at = now()
            WHERE state = 'pending' AND expires_at <= now()
            """
        )
        return int(result.split()[-1])

    async def add_record_ref(
        self,
        *,
        space_id: UUID,
        principal_id: str,
        provider_record_id: str,
        proposal_id: UUID | None,
        external_id: str | None,
        provenance: dict[str, Any],
    ) -> MemoryRecordRef:
        row = await self._pool().fetchrow(
            """
            INSERT INTO memory_record_refs
                (space_id, principal_id, provider_record_id, proposal_id, external_id, provenance)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (space_id, provider_record_id) DO UPDATE SET updated_at = now()
            RETURNING *
            """,
            space_id,
            principal_id,
            provider_record_id,
            proposal_id,
            external_id,
            json.dumps(provenance),
        )
        return _reference(row)

    async def get_record_ref(self, principal_id: str, reference_id: UUID) -> MemoryRecordRef | None:
        row = await self._pool().fetchrow(
            "SELECT * FROM memory_record_refs WHERE id = $1 AND principal_id = $2",
            reference_id,
            principal_id,
        )
        return _reference(row) if row else None

    async def find_external_ref(
        self, principal_id: str, space_id: UUID, external_id: str
    ) -> MemoryRecordRef | None:
        row = await self._pool().fetchrow(
            """
            SELECT * FROM memory_record_refs
            WHERE principal_id = $1 AND space_id = $2 AND external_id = $3
            """,
            principal_id,
            space_id,
            external_id,
        )
        return _reference(row) if row else None

    async def list_record_refs(self, principal_id: str, space_id: UUID) -> list[MemoryRecordRef]:
        rows = await self._pool().fetch(
            """
            SELECT * FROM memory_record_refs
            WHERE principal_id = $1 AND space_id = $2 AND status <> 'purged'
            ORDER BY created_at DESC
            """,
            principal_id,
            space_id,
        )
        return [_reference(row) for row in rows]

    async def set_record_status(self, principal_id: str, reference_id: UUID, status: str) -> None:
        await self._pool().execute(
            """
            UPDATE memory_record_refs SET status = $3, updated_at = now()
            WHERE id = $1 AND principal_id = $2
            """,
            reference_id,
            principal_id,
            status,
        )

    async def purge_record_content(self, principal_id: str, reference_id: UUID) -> None:
        await self._pool().execute(
            """
            UPDATE memory_proposals SET content = NULL, updated_at = now()
            WHERE id = (
                SELECT proposal_id FROM memory_record_refs WHERE id = $1 AND principal_id = $2
            )
            """,
            reference_id,
            principal_id,
        )
        await self.set_record_status(principal_id, reference_id, "purged")

    async def set_bridge_consent(
        self,
        principal_id: str,
        source_space_id: UUID,
        target_space_id: UUID,
        *,
        source_consented: bool,
        target_consented: bool,
    ) -> dict[str, Any]:
        row = await self._pool().fetchrow(
            """
            INSERT INTO memory_bridge_consents
                (source_space_id, target_space_id, principal_id,
                 source_consented, target_consented)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (source_space_id, target_space_id, principal_id) DO UPDATE SET
                source_consented = EXCLUDED.source_consented,
                target_consented = EXCLUDED.target_consented,
                updated_at = now()
            RETURNING source_space_id, target_space_id, source_consented, target_consented
            """,
            source_space_id,
            target_space_id,
            principal_id,
            source_consented,
            target_consented,
        )
        return dict(row)

    async def active_bridges(self, principal_id: str, target_space_id: UUID) -> list[MemorySpace]:
        rows = await self._pool().fetch(
            """
            SELECT s.* FROM memory_bridge_consents b
            JOIN memory_spaces s ON s.id = b.source_space_id
            JOIN memory_space_memberships source_member
              ON source_member.space_id = b.source_space_id
             AND source_member.principal_id = b.principal_id
            JOIN memory_space_memberships target_member
              ON target_member.space_id = b.target_space_id
             AND target_member.principal_id = b.principal_id
            WHERE b.principal_id = $1 AND b.target_space_id = $2
              AND b.source_consented AND b.target_consented
            """,
            principal_id,
            target_space_id,
        )
        return [_space(row) for row in rows]

    async def audit(
        self,
        principal_id: str,
        action: str,
        target_type: str,
        target_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._pool().execute(
            """
            INSERT INTO memory_audit (principal_id, action, target_type, target_id, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            principal_id,
            action,
            target_type,
            target_id,
            json.dumps(_audit_metadata(metadata)),
        )


class InMemoryMemoryRepository:
    persistent = False

    def __init__(self):
        self.settings: dict[str, UserMemorySettings] = {}
        self.spaces: dict[UUID, MemorySpace] = {}
        self.memberships: set[tuple[UUID, str]] = set()
        self.proposals: dict[UUID, MemoryProposal] = {}
        self.references: dict[UUID, MemoryRecordRef] = {}
        self.bridges: dict[tuple[UUID, UUID, str], dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def ping(self) -> bool:
        return True

    async def get_settings(self, principal_id: str) -> UserMemorySettings | None:
        return self.settings.get(principal_id)

    async def set_settings(
        self, principal_id: str, settings: UserMemorySettings
    ) -> UserMemorySettings:
        self.settings[principal_id] = settings
        return settings

    async def ensure_personal_space(
        self, principal_id: str, provider_namespace: str
    ) -> MemorySpace:
        for space in self.spaces.values():
            if space.kind == "personal" and space.owner_principal_id == principal_id:
                return space
        space = MemorySpace(uuid4(), "personal", principal_id, "Personal", provider_namespace, {})
        self.spaces[space.id] = space
        self.memberships.add((space.id, principal_id))
        return space

    async def create_game_space(
        self,
        principal_id: str,
        display_name: str,
        provider_namespace: str,
        metadata: dict[str, Any],
    ) -> MemorySpace:
        for space in self.spaces.values():
            if (
                space.kind == "game"
                and space.owner_principal_id == principal_id
                and space.display_name == display_name
            ):
                return space
        space = MemorySpace(
            uuid4(), "game", principal_id, display_name, provider_namespace, dict(metadata)
        )
        self.spaces[space.id] = space
        self.memberships.add((space.id, principal_id))
        return space

    async def get_space(self, principal_id: str, space_id: UUID) -> MemorySpace | None:
        return self.spaces.get(space_id) if (space_id, principal_id) in self.memberships else None

    async def list_spaces(self, principal_id: str) -> list[MemorySpace]:
        return [
            space for space in self.spaces.values() if (space.id, principal_id) in self.memberships
        ]

    async def add_proposal(
        self,
        *,
        space_id: UUID,
        principal_id: str,
        content: str,
        source_experience: str,
        source_chat_hash: str | None,
        metadata: dict[str, Any],
        pending_days: int,
    ) -> MemoryProposal:
        now = datetime.now(UTC)
        item = MemoryProposal(
            uuid4(),
            space_id,
            principal_id,
            content,
            "pending",
            source_experience,
            source_chat_hash,
            dict(metadata),
            now + timedelta(days=pending_days),
            now,
        )
        self.proposals[item.id] = item
        return item

    async def list_proposals(
        self, principal_id: str, state: str = "pending"
    ) -> list[MemoryProposal]:
        return sorted(
            (
                item
                for item in self.proposals.values()
                if item.principal_id == principal_id and item.state == state
            ),
            key=lambda item: item.created_at,
            reverse=True,
        )

    async def get_proposal(self, principal_id: str, proposal_id: UUID) -> MemoryProposal | None:
        item = self.proposals.get(proposal_id)
        return item if item and item.principal_id == principal_id else None

    async def transition_proposal(
        self, principal_id: str, proposal_id: UUID, state: str, content: str | None
    ) -> MemoryProposal | None:
        item = await self.get_proposal(principal_id, proposal_id)
        if item is None or item.state != "pending":
            return None
        updated = MemoryProposal(
            item.id,
            item.space_id,
            item.principal_id,
            None if state in {"rejected", "expired"} else content,
            state,
            item.source_experience,
            item.source_chat_hash,
            item.metadata,
            item.expires_at,
            item.created_at,
        )
        self.proposals[item.id] = updated
        return updated

    async def expire_proposals(self) -> int:
        expired = 0
        now = datetime.now(UTC)
        for item in list(self.proposals.values()):
            if item.state == "pending" and item.expires_at <= now:
                await self.transition_proposal(item.principal_id, item.id, "expired", None)
                expired += 1
        return expired

    async def add_record_ref(
        self,
        *,
        space_id: UUID,
        principal_id: str,
        provider_record_id: str,
        proposal_id: UUID | None,
        external_id: str | None,
        provenance: dict[str, Any],
    ) -> MemoryRecordRef:
        for item in self.references.values():
            if item.space_id == space_id and item.provider_record_id == provider_record_id:
                return item
        item = MemoryRecordRef(
            uuid4(),
            space_id,
            principal_id,
            provider_record_id,
            proposal_id,
            external_id,
            "active",
            dict(provenance),
        )
        self.references[item.id] = item
        return item

    async def get_record_ref(self, principal_id: str, reference_id: UUID) -> MemoryRecordRef | None:
        item = self.references.get(reference_id)
        return item if item and item.principal_id == principal_id else None

    async def find_external_ref(
        self, principal_id: str, space_id: UUID, external_id: str
    ) -> MemoryRecordRef | None:
        return next(
            (
                item
                for item in self.references.values()
                if item.principal_id == principal_id
                and item.space_id == space_id
                and item.external_id == external_id
            ),
            None,
        )

    async def list_record_refs(self, principal_id: str, space_id: UUID) -> list[MemoryRecordRef]:
        return [
            item
            for item in self.references.values()
            if item.principal_id == principal_id
            and item.space_id == space_id
            and item.status != "purged"
        ]

    async def set_record_status(self, principal_id: str, reference_id: UUID, status: str) -> None:
        item = await self.get_record_ref(principal_id, reference_id)
        if item:
            self.references[item.id] = MemoryRecordRef(
                item.id,
                item.space_id,
                item.principal_id,
                item.provider_record_id,
                item.proposal_id,
                item.external_id,
                status,
                item.provenance,
            )

    async def purge_record_content(self, principal_id: str, reference_id: UUID) -> None:
        item = await self.get_record_ref(principal_id, reference_id)
        if item and item.proposal_id:
            proposal = self.proposals.get(item.proposal_id)
            if proposal:
                self.proposals[proposal.id] = MemoryProposal(
                    proposal.id,
                    proposal.space_id,
                    proposal.principal_id,
                    None,
                    proposal.state,
                    proposal.source_experience,
                    proposal.source_chat_hash,
                    proposal.metadata,
                    proposal.expires_at,
                    proposal.created_at,
                )
        await self.set_record_status(principal_id, reference_id, "purged")

    async def set_bridge_consent(
        self,
        principal_id: str,
        source_space_id: UUID,
        target_space_id: UUID,
        *,
        source_consented: bool,
        target_consented: bool,
    ) -> dict[str, Any]:
        value = {
            "source_space_id": source_space_id,
            "target_space_id": target_space_id,
            "source_consented": source_consented,
            "target_consented": target_consented,
        }
        self.bridges[(source_space_id, target_space_id, principal_id)] = value
        return value

    async def active_bridges(self, principal_id: str, target_space_id: UUID) -> list[MemorySpace]:
        result = []
        for (source_id, target_id, owner), consent in self.bridges.items():
            if (
                owner == principal_id
                and target_id == target_space_id
                and consent["source_consented"]
                and consent["target_consented"]
                and (source_id, principal_id) in self.memberships
                and (target_id, principal_id) in self.memberships
            ):
                result.append(self.spaces[source_id])
        return result

    async def audit(
        self,
        principal_id: str,
        action: str,
        target_type: str,
        target_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.audit_events.append(
            {
                "principal_id": principal_id,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "metadata": _audit_metadata(metadata),
            }
        )
