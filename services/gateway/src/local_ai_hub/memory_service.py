from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .identity import PrincipalContext, pseudonymous_subject
from .memory_config import MemoryConfig
from .memory_provider import MemoryProvider, MemoryProviderError, MemoryScope, ProviderRecord
from .memory_repository import (
    MemoryProposal,
    MemoryRecordRef,
    MemoryRepository,
    MemorySpace,
    UserMemorySettings,
)

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S+", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


class MemoryAccessError(PermissionError):
    pass


class MemoryConflictError(RuntimeError):
    pass


def contains_probable_secret(content: str) -> bool:
    return any(pattern.search(content) for pattern in _SECRET_PATTERNS)


def _proposal_dict(item: MemoryProposal) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "space_id": str(item.space_id),
        "content": item.content,
        "state": item.state,
        "source_experience": item.source_experience,
        "metadata": item.metadata,
        "expires_at": item.expires_at.isoformat(),
        "created_at": item.created_at.isoformat(),
    }


def _space_dict(item: MemorySpace) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "kind": item.kind,
        "display_name": item.display_name,
        "provider_namespace": item.provider_namespace,
        "metadata": item.metadata,
    }


class MemoryService:
    def __init__(
        self,
        config: MemoryConfig,
        provider: MemoryProvider,
        repository: MemoryRepository,
        *,
        subject_hmac_fallback: str,
    ):
        self.config = config
        self.provider = provider
        self.repository = repository
        self.subject_hmac_fallback = subject_hmac_fallback
        self.start_error: str | None = None

    async def start(self) -> None:
        await self.repository.start()
        try:
            await self.provider.start()
        except Exception as exc:
            self.start_error = str(exc)
            if self.config.provider.required:
                await self.provider.close()
                await self.repository.close()
                raise
            logger.warning("optional memory provider is unavailable: %s", exc)

    async def close(self) -> None:
        await self.provider.close()
        await self.repository.close()

    async def ping(self) -> bool:
        repository_ready = await self.repository.ping()
        provider_ready = await self.provider.ping()
        if repository_ready and provider_ready:
            self.start_error = None
            return True
        return False

    def _subject_secret(self) -> str:
        return os.getenv(self.config.personal.subject_hmac_env) or self.subject_hmac_fallback

    def _subject(self, principal_id: str) -> str:
        return pseudonymous_subject(principal_id, self._subject_secret())

    def _opaque_session(self, session_id: str | None) -> str | None:
        if not session_id:
            return None
        return hmac.new(
            self._subject_secret().encode(), session_id.encode(), hashlib.sha256
        ).hexdigest()

    async def settings_for(self, principal_id: str) -> UserMemorySettings:
        stored = await self.repository.get_settings(principal_id)
        if stored is not None:
            return stored
        default = self.config.automatic.enabled and self.config.automatic.default_user_enabled
        return UserMemorySettings(default, default, default)

    async def update_settings(
        self,
        principal_id: str,
        *,
        enabled: bool,
        capture_enabled: bool,
        retrieval_enabled: bool,
    ) -> UserMemorySettings:
        settings = UserMemorySettings(enabled, capture_enabled, retrieval_enabled)
        stored = await self.repository.set_settings(principal_id, settings)
        await self.repository.audit(
            principal_id,
            "settings.update",
            "principal",
            principal_id,
            {
                "enabled": enabled,
                "capture_enabled": capture_enabled,
                "retrieval_enabled": retrieval_enabled,
            },
        )
        return stored

    async def personal_space(self, principal_id: str) -> MemorySpace:
        space = await self.repository.ensure_personal_space(
            principal_id, self.config.provider.namespace
        )
        migrate = getattr(self.provider, "migrate_legacy", None)
        if migrate is not None:
            migrated = await migrate(principal_id, self.scope_for(principal_id, space))
            if migrated:
                await self.repository.audit(
                    principal_id,
                    "legacy.migrate",
                    "space",
                    str(space.id),
                    {"records": migrated},
                )
        return space

    async def _space(self, principal_id: str, space_id: UUID) -> MemorySpace:
        space = await self.repository.get_space(principal_id, space_id)
        if space is None:
            raise MemoryAccessError("memory space was not found")
        return space

    def scope_for(
        self, principal_id: str, space: MemorySpace, *, session_id: str | None = None
    ) -> MemoryScope:
        subject = self._subject(principal_id)
        if space.kind == "personal":
            return MemoryScope(
                namespace=space.provider_namespace,
                workspace_id=str(space.id),
                context_id=self.config.personal.context,
                subject_id=subject,
                session_id=self._opaque_session(session_id),
            )
        return MemoryScope(
            namespace=space.provider_namespace,
            workspace_id=str(space.metadata.get("world_id") or space.id),
            context_id=str(space.metadata.get("campaign_id") or space.id),
            subject_id=str(space.metadata.get("player_id") or subject),
            session_id=self._opaque_session(session_id),
        )

    def automatic_experience_allowed(self, experience: str, capability: str | None) -> bool:
        if not self.config.automatic.enabled:
            return False
        if experience in self.config.automatic.excluded_experiences:
            return False
        return bool(capability and capability in self.config.automatic.capabilities)

    async def can_capture(self, principal_id: str, experience: str, capability: str | None) -> bool:
        settings = await self.settings_for(principal_id)
        return (
            self.provider.enabled
            and self.config.automatic.capture
            and self.automatic_experience_allowed(experience, capability)
            and settings.enabled
            and settings.capture_enabled
        )

    async def can_retrieve(
        self, principal_id: str, experience: str, capability: str | None
    ) -> bool:
        settings = await self.settings_for(principal_id)
        return (
            self.provider.enabled
            and self.config.automatic.retrieval
            and self.automatic_experience_allowed(experience, capability)
            and settings.enabled
            and settings.retrieval_enabled
        )

    async def context(
        self,
        principal_id: str,
        *,
        query: str,
        limit: int,
        experience: str,
        capability: str | None,
    ) -> list[ProviderRecord]:
        if not await self.can_retrieve(principal_id, experience, capability):
            return []
        target = await self.personal_space(principal_id)
        records = await self.provider.context_load(
            self.scope_for(principal_id, target), query=query, limit=limit
        )
        if not self.config.bridges.enabled:
            return records
        remaining = max(limit - len(records), 0)
        if remaining <= 0:
            return records
        for source in await self.repository.active_bridges(principal_id, target.id):
            bridge_records = await self.provider.context_load(
                self.scope_for(principal_id, source), query=query, limit=remaining
            )
            records.extend(
                ProviderRecord(
                    id=item.id,
                    content=item.content,
                    metadata={**item.metadata, "bridge_source_space_id": str(source.id)},
                    source=item.source,
                    importance=item.importance,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    forgotten=item.forgotten,
                    rank=item.rank,
                )
                for item in bridge_records
            )
            remaining = max(limit - len(records), 0)
            if remaining == 0:
                break
        return records[:limit]

    async def manual_search(
        self, principal_id: str, *, query: str, limit: int
    ) -> list[ProviderRecord]:
        space = await self.personal_space(principal_id)
        return await self.provider.context_load(
            self.scope_for(principal_id, space), query=query, limit=limit
        )

    async def add_approved(
        self,
        principal_id: str,
        *,
        content: str,
        source: str | None,
        metadata: dict[str, Any],
        importance: float,
        external_id: str | None = None,
        space: MemorySpace | None = None,
    ) -> tuple[ProviderRecord, MemoryRecordRef]:
        if contains_probable_secret(content):
            raise MemoryConflictError("memory content appears to contain a credential or secret")
        target = space or await self.personal_space(principal_id)
        if external_id:
            existing = await self.repository.find_external_ref(principal_id, target.id, external_id)
            if existing:
                records = await self.provider.list_records(
                    self.scope_for(principal_id, target), include_forgotten=True
                )
                record = next(
                    (item for item in records if item.id == existing.provider_record_id), None
                )
                if record is not None:
                    return record, existing
        record = await self.provider.ingest(
            self.scope_for(principal_id, target),
            content=content,
            source=source,
            metadata=metadata,
            importance=importance,
            record_id=external_id,
        )
        reference = await self.repository.add_record_ref(
            space_id=target.id,
            principal_id=principal_id,
            provider_record_id=record.id,
            proposal_id=None,
            external_id=external_id,
            provenance={"source": source or "manual"},
        )
        await self.repository.audit(
            principal_id,
            "record.create",
            "record",
            str(reference.id),
            {"source_provided": bool(source)},
        )
        return record, reference

    async def add_proposals(
        self,
        principal_id: str,
        *,
        candidates: list[dict[str, Any]],
        experience: str,
        chat_hash: str | None,
    ) -> list[MemoryProposal]:
        target = await self.personal_space(principal_id)
        created: list[MemoryProposal] = []
        seen: set[str] = set()
        for candidate in candidates[: self.config.automatic.max_candidates]:
            content = candidate.get("content")
            if not isinstance(content, str):
                continue
            content = content.strip()
            normalized = " ".join(content.lower().split())
            if len(content) < 3 or normalized in seen or contains_probable_secret(content):
                continue
            seen.add(normalized)
            created.append(
                await self.repository.add_proposal(
                    space_id=target.id,
                    principal_id=principal_id,
                    content=content,
                    source_experience=experience,
                    source_chat_hash=chat_hash,
                    metadata={
                        "kind": str(candidate.get("kind") or "fact"),
                        "importance": max(0.0, min(float(candidate.get("importance", 0.5)), 1.0)),
                    },
                    pending_days=self.config.retention.pending_days,
                )
            )
        return created

    async def proposals(self, principal_id: str, state: str = "pending") -> list[dict[str, Any]]:
        await self.repository.expire_proposals()
        return [
            _proposal_dict(item)
            for item in await self.repository.list_proposals(principal_id, state)
        ]

    async def approve_proposal(
        self, principal_id: str, proposal_id: UUID, content: str | None = None
    ) -> dict[str, Any]:
        proposal = await self.repository.get_proposal(principal_id, proposal_id)
        if proposal is None or proposal.state != "pending" or proposal.content is None:
            raise MemoryConflictError("proposal is not pending")
        if proposal.expires_at <= datetime.now(UTC):
            expired = await self.repository.transition_proposal(
                principal_id, proposal.id, "expired", None
            )
            if expired is not None:
                await self.repository.audit(
                    principal_id, "proposal.expire", "proposal", str(proposal.id)
                )
            raise MemoryConflictError("proposal has expired")
        approved_content = (content or proposal.content).strip()
        if not approved_content or contains_probable_secret(approved_content):
            raise MemoryConflictError("approved memory is empty or contains a probable secret")
        space = await self._space(principal_id, proposal.space_id)
        record = await self.provider.ingest(
            self.scope_for(principal_id, space),
            content=approved_content,
            source="proposal",
            metadata={"proposal_id": str(proposal.id), **proposal.metadata},
            importance=float(proposal.metadata.get("importance", 0.5)),
            record_id=str(proposal.id),
        )
        reference = await self.repository.add_record_ref(
            space_id=space.id,
            principal_id=principal_id,
            provider_record_id=record.id,
            proposal_id=proposal.id,
            external_id=None,
            provenance={"source_experience": proposal.source_experience},
        )
        transitioned = await self.repository.transition_proposal(
            principal_id, proposal.id, "approved", approved_content
        )
        if transitioned is None:
            raise MemoryConflictError("proposal changed while it was being approved")
        await self.repository.audit(principal_id, "proposal.approve", "proposal", str(proposal.id))
        return {"proposal": _proposal_dict(transitioned), "reference_id": str(reference.id)}

    async def reject_proposal(self, principal_id: str, proposal_id: UUID) -> dict[str, Any]:
        transitioned = await self.repository.transition_proposal(
            principal_id, proposal_id, "rejected", None
        )
        if transitioned is None:
            raise MemoryConflictError("proposal is not pending")
        await self.repository.audit(principal_id, "proposal.reject", "proposal", str(proposal_id))
        return _proposal_dict(transitioned)

    async def records(self, principal_id: str) -> list[dict[str, Any]]:
        space = await self.personal_space(principal_id)
        provider_records = await self.provider.list_records(
            self.scope_for(principal_id, space), include_forgotten=True
        )
        refs = {
            item.provider_record_id: item
            for item in await self.repository.list_record_refs(principal_id, space.id)
        }
        result: list[dict[str, Any]] = []
        for record in provider_records:
            reference = refs.get(record.id)
            if reference is None:
                reference = await self.repository.add_record_ref(
                    space_id=space.id,
                    principal_id=principal_id,
                    provider_record_id=record.id,
                    proposal_id=None,
                    external_id=None,
                    provenance={"source": "legacy-provider-record"},
                )
            result.append(
                {
                    "id": str(reference.id),
                    "provider_record_id": record.id,
                    "space_id": str(space.id),
                    "content": record.content,
                    "source": record.source,
                    "importance": record.importance,
                    "metadata": record.metadata,
                    "status": "forgotten" if record.forgotten else reference.status,
                    "created_at": record.created_at.isoformat() if record.created_at else None,
                    "updated_at": record.updated_at.isoformat() if record.updated_at else None,
                }
            )
        return result

    async def _record_target(
        self, principal_id: str, reference_id: UUID
    ) -> tuple[MemoryRecordRef, MemorySpace, MemoryScope]:
        reference = await self.repository.get_record_ref(principal_id, reference_id)
        if reference is None:
            raise MemoryAccessError("memory record was not found")
        space = await self._space(principal_id, reference.space_id)
        return reference, space, self.scope_for(principal_id, space)

    async def correct_record(
        self, principal_id: str, reference_id: UUID, content: str, reason: str
    ) -> dict[str, Any]:
        if contains_probable_secret(content):
            raise MemoryConflictError("memory content appears to contain a credential or secret")
        reference, _space, scope = await self._record_target(principal_id, reference_id)
        record = await self.provider.correct(
            scope,
            reference.provider_record_id,
            content=content,
            reason="user-correction" if reason else "correction",
        )
        await self.repository.set_record_status(principal_id, reference.id, "active")
        await self.repository.audit(
            principal_id,
            "record.correct",
            "record",
            str(reference.id),
            {"reason_provided": bool(reason)},
        )
        return {"id": str(reference.id), "content": record.content, "status": "active"}

    async def forget_record(
        self, principal_id: str, reference_id: UUID, reason: str
    ) -> dict[str, Any]:
        reference, _space, scope = await self._record_target(principal_id, reference_id)
        provider_reason = "user-request" if reason else "forget-request"
        if not await self.provider.forget(
            scope, reference.provider_record_id, reason=provider_reason
        ):
            raise MemoryProviderError("provider did not forget the record")
        await self.repository.set_record_status(principal_id, reference.id, "forgotten")
        await self.repository.audit(
            principal_id,
            "record.forget",
            "record",
            str(reference.id),
            {"reason_provided": bool(reason)},
        )
        return {"id": str(reference.id), "status": "forgotten"}

    async def purge_record(
        self, principal_id: str, reference_id: UUID, reason: str
    ) -> dict[str, Any]:
        reference, _space, scope = await self._record_target(principal_id, reference_id)
        provider_reason = "user-hard-purge" if reason else "hard-purge-request"
        if not await self.provider.purge(
            scope, reference.provider_record_id, reason=provider_reason
        ):
            raise MemoryProviderError("provider did not purge the record")
        await self.repository.purge_record_content(principal_id, reference.id)
        await self.repository.audit(
            principal_id,
            "record.purge",
            "record",
            str(reference.id),
            {"reason_provided": bool(reason)},
        )
        return {"id": str(reference.id), "status": "purged"}

    async def export_personal(self, principal_id: str) -> dict[str, Any]:
        space = await self.personal_space(principal_id)
        return {
            "format": "agent-ui-memory/1",
            "space": _space_dict(space),
            "provider_kind": self.provider.kind,
            "records": await self.provider.export(self.scope_for(principal_id, space)),
        }

    async def import_personal(
        self, principal_id: str, records: list[dict[str, Any]]
    ) -> dict[str, int]:
        imported = 0
        skipped = 0
        for item in records:
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                skipped += 1
                continue
            external_id = str(item.get("external_id") or item.get("record_id") or "") or None
            before = await self.personal_space(principal_id)
            existing = (
                await self.repository.find_external_ref(principal_id, before.id, external_id)
                if external_id
                else None
            )
            if existing:
                skipped += 1
                continue
            await self.add_approved(
                principal_id,
                content=content,
                source="import",
                metadata=dict(item.get("metadata") or {}),
                importance=float(item.get("importance", 0.5)),
                external_id=external_id,
                space=before,
            )
            imported += 1
        return {"imported": imported, "skipped": skipped}

    async def set_bridge(
        self,
        principal_id: str,
        source_space_id: UUID,
        target_space_id: UUID,
        *,
        source_consented: bool,
        target_consented: bool,
    ) -> dict[str, Any]:
        if not self.config.bridges.enabled:
            raise MemoryAccessError("memory bridges are disabled by the operator")
        source = await self._space(principal_id, source_space_id)
        target = await self._space(principal_id, target_space_id)
        allowed = any(
            item.source_kind == source.kind and item.target_kind == target.kind
            for item in self.config.bridges.operator_allowlist
        )
        if not allowed:
            raise MemoryAccessError("this bridge direction is not operator-allowlisted")
        consent = await self.repository.set_bridge_consent(
            principal_id,
            source.id,
            target.id,
            source_consented=source_consented,
            target_consented=target_consented,
        )
        await self.repository.audit(
            principal_id,
            "bridge.consent",
            "bridge",
            f"{source.id}->{target.id}",
            {
                "source_consented": source_consented,
                "target_consented": target_consented,
            },
        )
        return {
            **consent,
            "source_space_id": str(source.id),
            "target_space_id": str(target.id),
            "active": source_consented and target_consented,
        }

    async def spaces(self, principal_id: str) -> list[dict[str, Any]]:
        await self.personal_space(principal_id)
        return [_space_dict(item) for item in await self.repository.list_spaces(principal_id)]

    async def create_game_space(
        self,
        principal: PrincipalContext,
        *,
        display_name: str,
        namespace: str,
        world_id: str,
        campaign_id: str,
        player_id: str | None,
    ) -> dict[str, Any]:
        if principal.kind != "service":
            raise MemoryAccessError("game memory APIs require a service principal")
        space = await self.repository.create_game_space(
            principal.principal_id,
            display_name,
            namespace,
            {
                "world_id": world_id,
                "campaign_id": campaign_id,
                "player_id": player_id,
            },
        )
        return _space_dict(space)

    async def ingest_game_event(
        self,
        principal: PrincipalContext,
        *,
        space_id: UUID,
        external_id: str,
        content: str,
        metadata: dict[str, Any],
        session_id: str | None,
    ) -> dict[str, Any]:
        if principal.kind != "service":
            raise MemoryAccessError("game memory APIs require a service principal")
        space = await self._space(principal.principal_id, space_id)
        if space.kind != "game":
            raise MemoryAccessError("structured events require a game memory space")
        existing = await self.repository.find_external_ref(
            principal.principal_id, space.id, external_id
        )
        if existing:
            return {"reference_id": str(existing.id), "duplicate": True}
        record = await self.provider.ingest(
            self.scope_for(principal.principal_id, space, session_id=session_id),
            content=content,
            source="game-event",
            metadata=metadata,
            importance=0.7,
            record_id=external_id,
        )
        reference = await self.repository.add_record_ref(
            space_id=space.id,
            principal_id=principal.principal_id,
            provider_record_id=record.id,
            proposal_id=None,
            external_id=external_id,
            provenance={"source": "game-event"},
        )
        return {"reference_id": str(reference.id), "duplicate": False}

    async def game_context(
        self,
        principal: PrincipalContext,
        *,
        space_id: UUID,
        query: str,
        limit: int,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        if principal.kind != "service":
            raise MemoryAccessError("game memory APIs require a service principal")
        space = await self._space(principal.principal_id, space_id)
        if space.kind != "game":
            raise MemoryAccessError("game context requires a game memory space")
        records = await self.provider.context_load(
            self.scope_for(principal.principal_id, space, session_id=session_id),
            query=query,
            limit=limit,
        )
        return [
            {"record_id": item.id, "content": item.content, "metadata": item.metadata}
            for item in records
        ]

    async def status(self, principal_id: str) -> dict[str, Any]:
        settings = await self.settings_for(principal_id)
        space = await self.personal_space(principal_id)
        return {
            "contract": "agent-ui-memory/1",
            "provider": {
                "kind": self.provider.kind,
                "healthy": await self.ping(),
                "required": self.config.provider.required,
                "capabilities": sorted(self.provider.capabilities),
                "error": self.start_error,
            },
            "automatic": {
                "operator_enabled": self.config.automatic.enabled,
                "capture": self.config.automatic.capture,
                "retrieval": self.config.automatic.retrieval,
            },
            "user": asdict(settings),
            "personal_space": _space_dict(space),
            "repository_persistent": self.repository.persistent,
        }
