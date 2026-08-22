from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.background import BackgroundTask

from .api_models import MemoryCreate, RoutePreview
from .backends import BackendRuntimeRegistry
from .config import ProfileDocument, Settings, load_profiles
from .coordinator import ModelCoordinationError
from .memory import MemoryStore, NullMemoryStore, PostgresMemoryStore
from .observability import (
    CHAT_REQUESTS,
    HTTP_REQUESTS,
    MEMORY_RETRIEVALS,
    REQUEST_SECONDS,
    ROUTE_DECISIONS,
)
from .profiles import (
    ProfileRegistry,
    ResolvedProfile,
    UnavailableExperienceError,
    UnknownModelError,
)
from .routing import latest_user_text
from .transform import InvalidChatRequest, prepare_chat_payload, prepare_passthrough_payload

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = {"/health", "/health/live", "/health/ready", "/metrics"}
_DEFAULT_EXPERIENCE = {
    "chat": "chat",
    "responses": "chat",
    "completions": "code",
    "infill": "code",
    "embeddings": "embeddings",
    "rerank": "rerank",
    "image": "image",
}
_ACCEPTED_CAPABILITIES = {
    "chat": {"chat", "code", "story", "agent", "vision"},
    "responses": {"chat", "code", "story", "agent", "vision"},
    "completions": {"completions", "code"},
    "infill": {"infill", "code"},
    "embeddings": {"embeddings"},
    "rerank": {"rerank"},
    "image": {"image"},
}


@dataclass(slots=True)
class Runtime:
    backends: BackendRuntimeRegistry
    memory: MemoryStore


def _openai_error(
    message: str, status: int, error_type: str = "invalid_request_error"
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": error_type, "param": None, "code": None}},
    )


def _record_to_dict(record: Any) -> dict[str, Any]:
    data = asdict(record)
    data["id"] = str(data["id"])
    data["created_at"] = data["created_at"].isoformat()
    data["updated_at"] = data["updated_at"].isoformat()
    return data


def _experience_header(request: Request) -> str | None:
    return request.headers.get("X-Agent-UI-Experience") or request.headers.get("X-Local-AI-Profile")


def _user_header(request: Request, settings: Settings) -> str:
    return (
        request.headers.get("X-Agent-UI-User")
        or request.headers.get("X-Local-AI-User")
        or settings.default_user_id
    )


def _capability_supported(resolved: ResolvedProfile, operation: str) -> bool:
    accepted = _ACCEPTED_CAPABILITIES[operation]
    return any(resolved.model.has_capability(item) for item in accepted)


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    memory_store: MemoryStore | None = None,
) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    document = load_profiles(settings.profile_config_path)
    registry = ProfileRegistry(document)

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        store: MemoryStore = memory_store or NullMemoryStore()
        if memory_store is None and settings.memory_enabled:
            if not settings.database_url:
                message = "MEMORY_ENABLED is true but DATABASE_URL is not configured"
                if settings.memory_required:
                    raise RuntimeError(message)
                logger.warning("%s; continuing without shared memory", message)
            else:
                candidate = PostgresMemoryStore(settings.database_url)
                try:
                    await candidate.start()
                    store = candidate
                except Exception:
                    logger.exception("failed to initialize shared memory")
                    if settings.memory_required:
                        raise
                    store = NullMemoryStore()
        else:
            await store.start()

        app_instance.state.runtime = Runtime(
            backends=BackendRuntimeRegistry.build(
                settings,
                app_instance.state.registry.document,
                transport=transport,
            ),
            memory=store,
        )
        try:
            yield
        finally:
            await store.close()
            await app_instance.state.runtime.backends.close()

    application = FastAPI(
        title="Agent UI Gateway",
        version="0.3.0",
        description=(
            "OpenAI-compatible capability routing, backend coordination, and shared memory "
            "for operator-registered local or remote AI models."
        ),
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.registry = registry
    application.state.transport = transport

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Agent-UI-Experience",
            "X-Agent-UI-User",
            "X-Agent-UI-Memory-Namespace",
            "X-Local-AI-Profile",
            "X-Local-AI-User",
            "X-Local-AI-Memory-Namespace",
            "X-Reasoning-Effort",
            "X-Request-ID",
        ],
        expose_headers=[
            "X-Request-ID",
            "X-Agent-UI-Experience",
            "X-Agent-UI-Model",
            "X-Agent-UI-Backend",
            "X-Agent-UI-Route-Reason",
            "X-Local-AI-Profile",
            "X-Local-AI-Backend-Model",
            "X-Local-AI-Route-Reason",
        ],
    )

    @application.middleware("http")
    async def authentication_and_metrics(request: Request, call_next):
        started = time.monotonic()
        status = 500
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        try:
            if request.method != "OPTIONS" and request.url.path not in _PUBLIC_PATHS:
                expected = settings.gateway_api_key.get_secret_value()
                provided = request.headers.get("Authorization", "")
                if not provided.startswith("Bearer ") or not hmac.compare_digest(
                    provided.removeprefix("Bearer "), expected
                ):
                    response = _openai_error(
                        "invalid or missing API key", 401, "authentication_error"
                    )
                    status = response.status_code
                    response.headers["X-Request-ID"] = request_id
                    return response
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            route = request.scope.get("route")
            metric_path = getattr(route, "path", None) or (
                request.url.path if request.url.path in _PUBLIC_PATHS else "__unmatched__"
            )
            HTTP_REQUESTS.labels(
                method=request.method,
                path=metric_path,
                status=str(status),
            ).inc()
            logger.debug(
                "request method=%s path=%s status=%s duration=%.3f request_id=%s",
                request.method,
                request.url.path,
                status,
                time.monotonic() - started,
                request_id,
            )

    @application.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health")
    @application.get("/health/ready")
    async def health_ready(request: Request) -> JSONResponse:
        runtime: Runtime | None = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return JSONResponse(status_code=503, content={"status": "starting"})
        current: ProfileRegistry = request.app.state.registry
        configured_models = [model for model in current.document.models.values() if model.enabled]
        checks: dict[str, Any] = {"memory": await runtime.memory.ping()}
        checks["backends"] = await runtime.backends.status()
        if not configured_models:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "setup_required",
                    "checks": checks,
                    "message": "No enabled models are registered; apply a catalog overlay.",
                },
            )
        usable = any(model.backend in runtime.backends.runtimes for model in configured_models)
        ready = usable and (bool(checks["memory"]) or not settings.memory_required)
        status = "ok" if checks["memory"] else "degraded"
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": status if ready else "unavailable", "checks": checks},
        )

    @application.get("/metrics")
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @application.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        current: ProfileRegistry = request.app.state.registry
        return {"object": "list", "data": current.advertised_models()}

    @application.get("/api/setup/status")
    async def setup_status(request: Request) -> dict[str, Any]:
        current: ProfileRegistry = request.app.state.registry
        runtime: Runtime = request.app.state.runtime
        return {
            "version": 2,
            "setup_required": not bool(current.document.models),
            "backends": await runtime.backends.status(),
            "models": {
                model_id: {
                    "display_name": model.display_name or model_id,
                    "enabled": model.enabled,
                    "backend": model.backend,
                    "capabilities": sorted(model.capabilities),
                    "artifact": model.artifact.model_dump(exclude_none=True),
                }
                for model_id, model in current.document.models.items()
            },
            "experiences": {
                profile_id: {
                    "capability": profile.capability,
                    "available": current.experience_available(profile),
                    "description": profile.description,
                }
                for profile_id, profile in current.document.profiles.items()
            },
        }

    @application.get("/api/catalog")
    async def catalog(request: Request) -> dict[str, Any]:
        current: ProfileRegistry = request.app.state.registry
        return current.document.model_dump(mode="json", exclude_none=True)

    @application.post("/api/routes/preview")
    async def route_preview(preview: RoutePreview, request: Request) -> dict[str, Any]:
        current: ProfileRegistry = request.app.state.registry
        try:
            resolved = current.resolve(preview.model, preview.messages, preview.profile_override)
        except UnknownModelError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except UnavailableExperienceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "requested_model": resolved.requested_model,
            "experience": resolved.profile_id,
            "profile": resolved.profile_id,
            "model": resolved.backend_model,
            "backend_model": resolved.backend_model,
            "backend": resolved.backend_id,
            "capabilities": sorted(resolved.model.capabilities),
            "reason": resolved.route_reason,
        }

    @application.post("/api/admin/reload-profiles")
    @application.post("/api/admin/reload-catalog")
    async def reload_catalog(request: Request) -> dict[str, Any]:
        new_document: ProfileDocument = load_profiles(settings.profile_config_path)
        runtime: Runtime = request.app.state.runtime
        await runtime.backends.close()
        request.app.state.registry = ProfileRegistry(new_document)
        runtime.backends = BackendRuntimeRegistry.build(
            settings,
            new_document,
            transport=request.app.state.transport,
        )
        return {
            "status": "reloaded",
            "backends": sorted(new_document.backends),
            "models": sorted(new_document.models),
            "experiences": sorted(new_document.profiles),
            "profiles": sorted(new_document.profiles),
        }

    @application.get("/api/models/status")
    async def model_status(request: Request) -> dict[str, Any]:
        runtime: Runtime = request.app.state.runtime
        coordinated = [
            item for item in runtime.backends.runtimes.values() if item.coordinator is not None
        ]
        if len(coordinated) == 1:
            status = await coordinated[0].coordinator.status_payload()
            status["backend_id"] = coordinated[0].backend_id
            return status
        return {"backends": await runtime.backends.status()}

    @application.post("/api/memories")
    async def create_memory(memory: MemoryCreate, request: Request) -> JSONResponse:
        runtime: Runtime = request.app.state.runtime
        if not runtime.memory.enabled:
            return JSONResponse(status_code=503, content={"error": "shared memory is disabled"})
        record = await runtime.memory.add(
            user_id=memory.user_id or _user_header(request, settings),
            namespace=memory.namespace,
            content=memory.content,
            source=memory.source,
            metadata=memory.metadata,
            importance=memory.importance,
        )
        return JSONResponse(status_code=201, content=_record_to_dict(record))

    @application.get("/api/memories/search")
    async def search_memories(
        request: Request,
        q: str = Query(min_length=1, max_length=2000),
        namespace: list[str] | None = Query(default=None, max_length=100),
        limit: int = Query(default=10, ge=1, le=30),
    ) -> dict[str, Any]:
        runtime: Runtime = request.app.state.runtime
        records = await runtime.memory.search(
            user_id=_user_header(request, settings),
            namespaces=namespace or ["general", "user", "projects"],
            query=q,
            limit=limit,
        )
        return {"data": [_record_to_dict(record) for record in records]}

    async def parse_body(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _openai_error("request body must be valid JSON", 400)
        if not isinstance(body, dict):
            return _openai_error("request body must be a JSON object", 400)
        return body

    async def proxy_operation(
        request: Request,
        operation: str,
        *,
        chat: bool = False,
    ) -> Response:
        parsed = await parse_body(request)
        if isinstance(parsed, JSONResponse):
            return parsed
        body = parsed
        requested_model = body.get("model", _DEFAULT_EXPERIENCE[operation])
        if not isinstance(requested_model, str):
            return _openai_error("model must be a string", 400)
        messages: list[dict[str, Any]] = []
        if chat:
            raw_messages = body.get("messages")
            if not isinstance(raw_messages, list):
                return _openai_error("messages must be an array", 400)
            messages = raw_messages

        current: ProfileRegistry = request.app.state.registry
        try:
            resolved = current.resolve(
                requested_model,
                messages,
                _experience_header(request),
            )
        except UnknownModelError as exc:
            return _openai_error(str(exc), 404)
        except UnavailableExperienceError as exc:
            return _openai_error(str(exc), 503, "model_unavailable")
        if not _capability_supported(resolved, operation):
            accepted = ", ".join(sorted(_ACCEPTED_CAPABILITIES[operation]))
            return _openai_error(
                f"model {resolved.backend_model!r} cannot serve {operation}; "
                f"declare one of these capabilities: {accepted}",
                400,
            )

        runtime: Runtime = request.app.state.runtime
        memories = []
        if (
            chat
            and runtime.memory.enabled
            and resolved.profile.memory.enabled
            and settings.memory_top_k > 0
        ):
            namespaces = list(resolved.profile.memory.namespaces)
            extra_namespace = request.headers.get(
                "X-Agent-UI-Memory-Namespace"
            ) or request.headers.get("X-Local-AI-Memory-Namespace")
            if extra_namespace and extra_namespace not in namespaces:
                namespaces.append(extra_namespace)
            try:
                memories = await runtime.memory.search(
                    user_id=_user_header(request, settings),
                    namespaces=namespaces,
                    query=latest_user_text(messages),
                    limit=settings.memory_top_k,
                )
                MEMORY_RETRIEVALS.labels(
                    profile=resolved.profile_id,
                    result="hit" if memories else "miss",
                ).inc()
            except Exception:
                logger.exception("memory retrieval failed")
                MEMORY_RETRIEVALS.labels(profile=resolved.profile_id, result="error").inc()

        try:
            if chat:
                payload = prepare_chat_payload(
                    body,
                    resolved,
                    memories,
                    settings.memory_max_chars,
                    request.headers.get("X-Reasoning-Effort"),
                )
            else:
                payload = prepare_passthrough_payload(
                    body,
                    resolved,
                    request.headers.get("X-Reasoning-Effort"),
                )
        except InvalidChatRequest as exc:
            return _openai_error(str(exc), 400)

        if settings.fixed_backend_model and resolved.backend_model != settings.fixed_backend_model:
            return _openai_error(
                f"fixed-model fallback currently serves {settings.fixed_backend_model!r}",
                503,
                "model_unavailable",
            )

        try:
            backend_runtime = runtime.backends.get(resolved.backend_id)
        except RuntimeError as exc:
            return _openai_error(str(exc), 503, "backend_unavailable")

        stream = bool(payload.get("stream", False)) and operation in {
            "chat",
            "responses",
            "completions",
            "infill",
        }
        stream_label = "true" if stream else "false"
        CHAT_REQUESTS.labels(
            profile=resolved.profile_id,
            backend=resolved.backend_model,
            stream=stream_label,
        ).inc()
        ROUTE_DECISIONS.labels(
            requested=requested_model,
            profile=resolved.profile_id,
            backend=resolved.backend_model,
        ).inc()
        timer_started = time.monotonic()
        lease = backend_runtime.lease()
        await lease.__aenter__()
        upstream_response: httpx.Response | None = None
        cleanup_lock = asyncio.Lock()
        cleanup_done = False

        async def cleanup_request() -> None:
            nonlocal cleanup_done
            async with cleanup_lock:
                if cleanup_done:
                    return
                cleanup_done = True
                try:
                    if upstream_response is not None:
                        await upstream_response.aclose()
                finally:
                    try:
                        await lease.__aexit__(None, None, None)
                    finally:
                        REQUEST_SECONDS.labels(
                            profile=resolved.profile_id,
                            backend=resolved.backend_model,
                            stream=stream_label,
                        ).observe(time.monotonic() - timer_started)

        try:
            await backend_runtime.ensure_loaded(
                resolved.model.upstream_model or resolved.backend_model
            )
            upstream_response = await backend_runtime.request(
                operation,
                payload,
                stream=stream,
            )
        except ModelCoordinationError as exc:
            await cleanup_request()
            return _openai_error(str(exc), 503, "model_unavailable")
        except (httpx.HTTPError, RuntimeError) as exc:
            await cleanup_request()
            return _openai_error(f"upstream request failed: {exc}", 502, "upstream_error")
        except BaseException:
            await cleanup_request()
            raise

        response_headers = {
            "X-Agent-UI-Experience": resolved.profile_id,
            "X-Agent-UI-Model": resolved.backend_model,
            "X-Agent-UI-Backend": resolved.backend_id,
            "X-Agent-UI-Route-Reason": resolved.route_reason[:240],
            "X-Local-AI-Profile": resolved.profile_id,
            "X-Local-AI-Backend-Model": resolved.backend_model,
            "X-Local-AI-Route-Reason": resolved.route_reason[:240],
        }
        try:
            if upstream_response.is_error:
                content = await upstream_response.aread()
                status_code = upstream_response.status_code
                response_headers["content-type"] = upstream_response.headers.get(
                    "content-type", "application/json"
                )
                await cleanup_request()
                return Response(content=content, status_code=status_code, headers=response_headers)
            if stream:
                response_headers["content-type"] = upstream_response.headers.get(
                    "content-type", "text/event-stream"
                )

                async def iterator():
                    try:
                        async for chunk in upstream_response.aiter_raw():
                            yield chunk
                    finally:
                        await cleanup_request()

                return StreamingResponse(
                    iterator(),
                    status_code=upstream_response.status_code,
                    headers=response_headers,
                    background=BackgroundTask(cleanup_request),
                )
            content = await upstream_response.aread()
            status_code = upstream_response.status_code
            response_headers["content-type"] = upstream_response.headers.get(
                "content-type", "application/json"
            )
            await cleanup_request()
            return Response(content=content, status_code=status_code, headers=response_headers)
        except BaseException:
            await cleanup_request()
            raise

    @application.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        return await proxy_operation(request, "chat", chat=True)

    @application.post("/v1/responses")
    async def responses(request: Request) -> Response:
        return await proxy_operation(request, "responses")

    @application.post("/v1/completions")
    async def completions(request: Request) -> Response:
        return await proxy_operation(request, "completions")

    @application.post("/infill")
    @application.post("/v1/infill")
    async def infill(request: Request) -> Response:
        return await proxy_operation(request, "infill")

    @application.post("/v1/embeddings")
    async def embeddings(request: Request) -> Response:
        return await proxy_operation(request, "embeddings")

    @application.post("/v1/rerank")
    @application.post("/v1/reranking")
    async def rerank(request: Request) -> Response:
        return await proxy_operation(request, "rerank")

    @application.post("/v1/images/generations")
    async def images(request: Request) -> Response:
        return await proxy_operation(request, "image")

    return application


app = create_app()
