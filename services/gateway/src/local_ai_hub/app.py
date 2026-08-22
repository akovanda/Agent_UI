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
from .config import Settings, load_profiles
from .coordinator import LlamaModelCoordinator, ModelCoordinationError
from .gpu import GpuGate
from .memory import MemoryStore, NullMemoryStore, PostgresMemoryStore
from .observability import (
    CHAT_REQUESTS,
    HTTP_REQUESTS,
    MEMORY_RETRIEVALS,
    REQUEST_SECONDS,
    ROUTE_DECISIONS,
)
from .profiles import ProfileRegistry, ResolvedProfile, UnknownModelError
from .routing import latest_user_text
from .transform import InvalidChatRequest, prepare_chat_payload
from .upstream import LlamaUpstream

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = {"/health", "/health/live", "/health/ready", "/metrics"}


@dataclass(slots=True)
class Runtime:
    client: httpx.AsyncClient
    coordinator: LlamaModelCoordinator
    upstream: LlamaUpstream
    gpu_gate: GpuGate
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
    registry = ProfileRegistry(load_profiles(settings.profile_config_path))

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        timeout = httpx.Timeout(
            connect=settings.upstream_connect_timeout_seconds,
            read=None,
            write=settings.upstream_write_timeout_seconds,
            pool=60.0,
        )
        client_headers: dict[str, str] = {}
        llama_api_key = settings.llama_api_key.get_secret_value()
        if llama_api_key:
            client_headers["Authorization"] = f"Bearer {llama_api_key}"
        client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers=client_headers,
        )
        store: MemoryStore = memory_store or NullMemoryStore()
        if memory_store is None and settings.memory_enabled:
            if not settings.database_url:
                message = "MEMORY_ENABLED is true but DATABASE_URL is not configured"
                if settings.memory_required:
                    await client.aclose()
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
                        await client.aclose()
                        raise
                    store = NullMemoryStore()
        else:
            await store.start()

        gpu_gate = GpuGate(settings.gpu_max_concurrent_requests)
        coordinator = LlamaModelCoordinator(
            client=client,
            base_url=settings.llama_base_url,
            mode=settings.model_coordinator_mode,
            timeout_seconds=settings.model_load_timeout_seconds,
            poll_interval_seconds=settings.model_poll_interval_seconds,
            transition_lock=gpu_gate.transition_lock,
        )
        app_instance.state.runtime = Runtime(
            client=client,
            coordinator=coordinator,
            upstream=LlamaUpstream(client, settings.llama_api_base_url),
            gpu_gate=gpu_gate,
            memory=store,
        )
        try:
            yield
        finally:
            await store.close()
            await client.aclose()

    application = FastAPI(
        title="Local AI Hub Gateway",
        version="0.2.0",
        description="OpenAI-compatible routing, model coordination, and shared-memory gateway.",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.registry = registry

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Local-AI-Profile",
            "X-Local-AI-User",
            "X-Local-AI-Memory-Namespace",
            "X-Reasoning-Effort",
        ],
        expose_headers=[
            "X-Request-ID",
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
        checks: dict[str, Any] = {"memory": await runtime.memory.ping()}
        try:
            response = await runtime.client.get(f"{settings.llama_base_url.rstrip('/')}/health")
            checks["llama"] = response.is_success
        except httpx.HTTPError:
            checks["llama"] = False
        ready = bool(checks["llama"]) and (bool(checks["memory"]) or not settings.memory_required)
        fully_healthy = all(checks.values())
        status = "ok" if fully_healthy else "degraded" if ready else "unavailable"
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": status, "checks": checks},
        )

    @application.get("/metrics")
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @application.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        current: ProfileRegistry = request.app.state.registry
        return {"object": "list", "data": current.advertised_models()}

    @application.post("/api/routes/preview")
    async def route_preview(preview: RoutePreview, request: Request) -> dict[str, Any]:
        current: ProfileRegistry = request.app.state.registry
        try:
            resolved = current.resolve(preview.model, preview.messages, preview.profile_override)
        except UnknownModelError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "requested_model": resolved.requested_model,
            "profile": resolved.profile_id,
            "backend_model": resolved.backend_model,
            "reason": resolved.route_reason,
        }

    @application.post("/api/admin/reload-profiles")
    async def reload_profiles(request: Request) -> dict[str, Any]:
        document = load_profiles(settings.profile_config_path)
        request.app.state.registry = ProfileRegistry(document)
        return {"status": "reloaded", "profiles": sorted(document.profiles)}

    @application.get("/api/models/status")
    async def model_status(request: Request) -> dict[str, Any]:
        runtime: Runtime = request.app.state.runtime
        try:
            return await runtime.coordinator.status_payload()
        except (httpx.HTTPError, ModelCoordinationError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @application.post("/api/memories")
    async def create_memory(memory: MemoryCreate, request: Request) -> JSONResponse:
        runtime: Runtime = request.app.state.runtime
        if not runtime.memory.enabled:
            return JSONResponse(status_code=503, content={"error": "shared memory is disabled"})
        user_id = (
            memory.user_id
            or request.headers.get("X-Local-AI-User")
            or settings.default_user_id
        )
        record = await runtime.memory.add(
            user_id=user_id,
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
        user_id = request.headers.get("X-Local-AI-User") or settings.default_user_id
        records = await runtime.memory.search(
            user_id=user_id,
            namespaces=namespace or ["general", "user", "projects", "infrastructure"],
            query=q,
            limit=limit,
        )
        return {"data": [_record_to_dict(record) for record in records]}

    @application.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        runtime: Runtime = request.app.state.runtime
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _openai_error("request body must be valid JSON", 400)
        if not isinstance(body, dict):
            return _openai_error("request body must be a JSON object", 400)

        requested_model = body.get("model", "assistant")
        if not isinstance(requested_model, str):
            return _openai_error("model must be a string", 400)
        messages = body.get("messages")
        if not isinstance(messages, list):
            return _openai_error("messages must be an array", 400)

        current: ProfileRegistry = request.app.state.registry
        try:
            resolved: ResolvedProfile = current.resolve(
                requested_model,
                messages,
                request.headers.get("X-Local-AI-Profile"),
            )
        except UnknownModelError as exc:
            return _openai_error(str(exc), 404)

        memories = []
        if (
            runtime.memory.enabled
            and resolved.profile.memory.enabled
            and settings.memory_top_k > 0
        ):
            namespaces = list(resolved.profile.memory.namespaces)
            extra_namespace = request.headers.get("X-Local-AI-Memory-Namespace")
            if extra_namespace and extra_namespace not in namespaces:
                namespaces.append(extra_namespace)
            try:
                memories = await runtime.memory.search(
                    user_id=request.headers.get("X-Local-AI-User") or settings.default_user_id,
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
            payload = prepare_chat_payload(
                body,
                resolved,
                memories,
                settings.memory_max_chars,
                request.headers.get("X-Reasoning-Effort"),
            )
        except InvalidChatRequest as exc:
            return _openai_error(str(exc), 400)

        if (
            settings.fixed_backend_model
            and resolved.backend_model != settings.fixed_backend_model
        ):
            return _openai_error(
                "fixed-model fallback currently serves "
                f"{settings.fixed_backend_model!r}; switch the fallback before requesting "
                f"{resolved.backend_model!r}",
                503,
                "model_unavailable",
            )

        stream = bool(payload.get("stream", False))
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
        lease = runtime.gpu_gate.lease()
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
            await runtime.coordinator.ensure_loaded(resolved.backend_model)
            upstream_response = await runtime.upstream.chat(payload)
        except ModelCoordinationError as exc:
            await cleanup_request()
            return _openai_error(str(exc), 503, "model_unavailable")
        except httpx.HTTPError as exc:
            await cleanup_request()
            return _openai_error(f"upstream request failed: {exc}", 502, "upstream_error")
        except BaseException:
            await cleanup_request()
            raise

        response_headers = {
            "X-Local-AI-Profile": resolved.profile_id,
            "X-Local-AI-Backend-Model": resolved.backend_model,
            "X-Local-AI-Route-Reason": resolved.route_reason[:240],
        }

        try:
            if upstream_response.is_error:
                content = await upstream_response.aread()
                status_code = upstream_response.status_code
                content_type = upstream_response.headers.get("content-type", "application/json")
                response_headers["content-type"] = content_type
                await cleanup_request()
                return Response(content=content, status_code=status_code, headers=response_headers)

            if stream:
                content_type = upstream_response.headers.get("content-type", "text/event-stream")
                response_headers["content-type"] = content_type

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
            content_type = upstream_response.headers.get("content-type", "application/json")
            response_headers["content-type"] = content_type
            await cleanup_request()
            return Response(content=content, status_code=status_code, headers=response_headers)
        except BaseException:
            await cleanup_request()
            raise


    return application


app = create_app()
