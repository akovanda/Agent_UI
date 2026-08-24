from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .api_models import (
    BridgeConsentUpdate,
    GameContextRequest,
    GameEventCreate,
    GameSpaceCreate,
    MemoryCorrection,
    MemoryImport,
    MemoryReason,
    MemorySettingsUpdate,
    ProposalApproval,
)
from .identity import PrincipalContext
from .memory_service import MemoryService
from .memory_ui import MEMORY_PAGE

router = APIRouter()


def _service(request: Request) -> MemoryService:
    return request.app.state.runtime.memory_service


def _principal(request: Request) -> PrincipalContext:
    return request.state.principal


def _csrf(request: Request) -> None:
    principal = _principal(request)
    if principal.cookie_authenticated and request.headers.get("X-Agent-UI-CSRF") != "1":
        from .memory_service import MemoryAccessError

        raise MemoryAccessError("missing same-origin mutation header")


@router.get("/memory", response_class=HTMLResponse)
async def memory_page() -> str:
    return MEMORY_PAGE


@router.get("/api/memory/v1/status")
async def memory_status(request: Request) -> dict[str, Any]:
    return await _service(request).status(_principal(request).principal_id)


@router.patch("/api/memory/v1/settings")
async def memory_settings(body: MemorySettingsUpdate, request: Request) -> dict[str, Any]:
    _csrf(request)
    value = await _service(request).update_settings(
        _principal(request).principal_id,
        enabled=body.enabled,
        capture_enabled=body.capture_enabled,
        retrieval_enabled=body.retrieval_enabled,
    )
    return {
        "enabled": value.enabled,
        "capture_enabled": value.capture_enabled,
        "retrieval_enabled": value.retrieval_enabled,
    }


@router.get("/api/memory/v1/spaces")
async def memory_spaces(request: Request) -> dict[str, Any]:
    return {"data": await _service(request).spaces(_principal(request).principal_id)}


@router.get("/api/memory/v1/proposals")
async def memory_proposals(request: Request, state: str = "pending") -> dict[str, Any]:
    return {"data": await _service(request).proposals(_principal(request).principal_id, state)}


@router.post("/api/memory/v1/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: UUID, body: ProposalApproval, request: Request
) -> dict[str, Any]:
    _csrf(request)
    return await _service(request).approve_proposal(
        _principal(request).principal_id, proposal_id, body.content
    )


@router.post("/api/memory/v1/proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: UUID, request: Request) -> dict[str, Any]:
    _csrf(request)
    return await _service(request).reject_proposal(_principal(request).principal_id, proposal_id)


@router.get("/api/memory/v1/records")
async def memory_records(request: Request) -> dict[str, Any]:
    return {"data": await _service(request).records(_principal(request).principal_id)}


@router.patch("/api/memory/v1/records/{reference_id}")
async def correct_memory(
    reference_id: UUID, body: MemoryCorrection, request: Request
) -> dict[str, Any]:
    _csrf(request)
    return await _service(request).correct_record(
        _principal(request).principal_id, reference_id, body.content, body.reason
    )


@router.post("/api/memory/v1/records/{reference_id}/forget")
async def forget_memory(reference_id: UUID, body: MemoryReason, request: Request) -> dict[str, Any]:
    _csrf(request)
    return await _service(request).forget_record(
        _principal(request).principal_id, reference_id, body.reason
    )


@router.delete("/api/memory/v1/records/{reference_id}")
async def purge_memory(reference_id: UUID, body: MemoryReason, request: Request) -> dict[str, Any]:
    _csrf(request)
    return await _service(request).purge_record(
        _principal(request).principal_id, reference_id, body.reason
    )


@router.get("/api/memory/v1/export")
async def export_memory(request: Request) -> JSONResponse:
    content = await _service(request).export_personal(_principal(request).principal_id)
    return JSONResponse(
        content=content,
        headers={"Content-Disposition": 'attachment; filename="agent-ui-memory.json"'},
    )


@router.post("/api/memory/v1/import")
async def import_memory(body: MemoryImport, request: Request) -> dict[str, int]:
    _csrf(request)
    if body.format != "agent-ui-memory/1":
        from .memory_service import MemoryConflictError

        raise MemoryConflictError("unsupported memory import format")
    return await _service(request).import_personal(_principal(request).principal_id, body.records)


@router.post("/api/memory/v1/bridges")
async def update_bridge(body: BridgeConsentUpdate, request: Request) -> dict[str, Any]:
    _csrf(request)
    return await _service(request).set_bridge(
        _principal(request).principal_id,
        UUID(body.source_space_id),
        UUID(body.target_space_id),
        source_consented=body.source_consented,
        target_consented=body.target_consented,
    )


@router.post("/api/memory/v1/internal/spaces", status_code=201)
async def create_game_space(body: GameSpaceCreate, request: Request) -> dict[str, Any]:
    return await _service(request).create_game_space(
        _principal(request),
        display_name=body.display_name,
        namespace=body.namespace,
        world_id=body.world_id,
        campaign_id=body.campaign_id,
        player_id=body.player_id,
    )


@router.post("/api/memory/v1/internal/spaces/{space_id}/events", status_code=201)
async def ingest_game_event(
    space_id: UUID, body: GameEventCreate, request: Request
) -> dict[str, Any]:
    return await _service(request).ingest_game_event(
        _principal(request),
        space_id=space_id,
        external_id=body.external_id,
        content=body.content,
        metadata=body.metadata,
        session_id=body.session_id,
    )


@router.post("/api/memory/v1/internal/context")
async def load_game_context(body: GameContextRequest, request: Request) -> dict[str, Any]:
    records = await _service(request).game_context(
        _principal(request),
        space_id=UUID(body.space_id),
        query=body.query,
        limit=body.limit,
        session_id=body.session_id,
    )
    return {"data": records}
