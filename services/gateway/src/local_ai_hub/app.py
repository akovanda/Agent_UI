from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.background import BackgroundTask

from .api_models import MemoryCreate, RoutePreview
from .config import Endpoint, Provider, RegistryDocument, Settings, load_registry
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
from .profiles import (
    ProfileRegistry,
    ResolvedProfile,
    UnavailableProfileError,
    UnknownModelError,
)
from .routing import latest_user_text, route_prefixes
from .transform import InvalidChatRequest, PreparedChat, prepare_chat_payload
from .upstream import ProviderConfigurationError, ProviderUpstream

logger = logging.getLogger(__name__)

_PUBLIC_PATHS = {
    "/health",
    "/health/live",
    "/health/ready",
    "/metrics",
    "/api/registry/schema",
}


@dataclass(slots=True)
class Runtime:
    client: httpx.AsyncClient
    upstream: ProviderUpstream
    coordinators: dict[str, LlamaModelCoordinator]
    gates: dict[str, GpuGate]
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


def _health_url(provider: Provider) -> str | None:
    if not provider.health_path:
        return None
    if provider.health_path.startswith(("http://", "https://")):
        return provider.health_path
    base = provider.control_url or provider.base_url
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base.rstrip('/')}/{provider.health_path.lstrip('/')}"


@asynccontextmanager
async def _null_lease() -> AsyncIterator[None]:
    yield


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
    registry = ProfileRegistry(load_registry(settings.registry_config_path))

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        timeout = httpx.Timeout(
            connect=settings.upstream_connect_timeout_seconds,
            read=None,
            write=settings.upstream_write_timeout_seconds,
            pool=60.0,
        )
        client = httpx.AsyncClient(timeout=timeout, transport=transport)
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

        app_instance.state.runtime = Runtime(
            client=client,
            upstream=ProviderUpstream(client),
            coordinators={},
            gates={},
            memory=store,
        )
        try:
            yield
        finally:
            await store.close()
            await client.aclose()

    application = FastAPI(
        title="Agent UI Gateway",
        version="0.3.0",
        description=(
            "Capability-aware OpenAI-compatible gateway for locally registered and "
            "remote inference providers."
        ),
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
            "X-Agent-UI-Profile",
            "X-Agent-UI-User",
            "X-Agent-UI-Memory-Namespace",
            "X-Local-AI-Profile",
            "X-Local-AI-User",
            "X-Local-AI-Memory-Namespace",
            "X-Reasoning-Effort",
        ],
        expose_headers=[
            "X-Request-ID",
            "X-Agent-UI-Profile",
            "X-Agent-UI-Model",
            "X-Agent-UI-Provider",
            "X-Agent-UI-Route-Reason",
            "X-Agent-UI-Reasoning-Requested",
            "X-Agent-UI-Reasoning-Applied",
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

    def current_registry(request: Request) -> ProfileRegistry:
        return request.app.state.registry

    def profile_header(request: Request) -> str | None:
        return request.headers.get("X-Agent-UI-Profile") or request.headers.get(
            "X-Local-AI-Profile"
        )

    def user_header(request: Request) -> str:
        return (
            request.headers.get("X-Agent-UI-User")
            or request.headers.get("X-Local-AI-User")
            or settings.default_user_id
        )

    def namespace_header(request: Request) -> str | None:
        return request.headers.get("X-Agent-UI-Memory-Namespace") or request.headers.get(
            "X-Local-AI-Memory-Namespace"
        )

    def provider_gate(runtime: Runtime, resolved: ResolvedProfile) -> GpuGate | None:
        provider = resolved.provider
        if provider.type != "llama_cpp" and not provider.resource_group:
            return None
        key = provider.resource_group or f"provider:{resolved.provider_id}"
        gate = runtime.gates.get(key)
        if gate is None:
            gate = GpuGate(provider.max_concurrency)
            runtime.gates[key] = gate
        return gate

    def provider_coordinator(
        runtime: Runtime,
        resolved: ResolvedProfile,
        gate: GpuGate | None,
    ) -> LlamaModelCoordinator | None:
        provider = resolved.provider
        if provider.type != "llama_cpp":
            return None
        coordinator = runtime.coordinators.get(resolved.provider_id)
        if coordinator is not None:
            return coordinator
        if gate is None:
            gate = GpuGate(provider.max_concurrency)
            runtime.gates[f"provider:{resolved.provider_id}"] = gate
        control_url = provider.control_url or provider.base_url.removesuffix("/v1")
        coordinator = LlamaModelCoordinator(
            client=runtime.client,
            base_url=control_url,
            mode=settings.model_coordinator_mode,
            timeout_seconds=settings.model_load_timeout_seconds,
            poll_interval_seconds=settings.model_poll_interval_seconds,
            transition_lock=gate.transition_lock,
        )
        runtime.coordinators[resolved.provider_id] = coordinator
        return coordinator

    def response_headers(
        resolved: ResolvedProfile,
        prepared: PreparedChat | None = None,
    ) -> dict[str, str]:
        values = {
            "X-Agent-UI-Profile": resolved.profile_id,
            "X-Agent-UI-Model": resolved.model_id,
            "X-Agent-UI-Provider": resolved.provider_id,
            "X-Agent-UI-Route-Reason": resolved.route_reason[:240],
            # Legacy headers remain during the 0.3 transition.
            "X-Local-AI-Profile": resolved.profile_id,
            "X-Local-AI-Backend-Model": resolved.model_id,
            "X-Local-AI-Route-Reason": resolved.route_reason[:240],
        }
        if prepared and prepared.requested_effort:
            values["X-Agent-UI-Reasoning-Requested"] = prepared.requested_effort
        if prepared and prepared.applied_effort:
            values["X-Agent-UI-Reasoning-Applied"] = prepared.applied_effort
        return values

    async def proxy_request(
        request: Request,
        resolved: ResolvedProfile,
        payload: dict[str, Any],
        endpoint: Endpoint,
        *,
        stream: bool,
        prepared: PreparedChat | None = None,
    ) -> Response:
        runtime: Runtime = request.app.state.runtime
        stream_label = "true" if stream else "false"
        if endpoint == "chat":
            CHAT_REQUESTS.labels(
                profile=resolved.profile_id,
                backend=resolved.model_id,
                stream=stream_label,
            ).inc()
        ROUTE_DECISIONS.labels(
            requested=resolved.requested_model,
            profile=resolved.profile_id,
            backend=resolved.model_id,
        ).inc()
        timer_started = time.monotonic()
        gate = provider_gate(runtime, resolved)
        lease = gate.lease() if gate is not None else _null_lease()
        await lease.__aenter__()
        upstream_response: httpx.Response | None = None
        cleanup_lock = asyncio.Lock()
        cleanup_done = False

        async def cleanup() -> None:
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
                            backend=resolved.model_id,
                            stream=stream_label,
                        ).observe(time.monotonic() - timer_started)

        try:
            coordinator = provider_coordinator(runtime, resolved, gate)
            if coordinator is not None:
                await coordinator.ensure_loaded(resolved.coordinator_model)
            upstream_response = await runtime.upstream.request(
                resolved.provider,
                endpoint,
                payload,
                stream=stream,
            )
        except ModelCoordinationError as exc:
            await cleanup()
            return _openai_error(str(exc), 503, "model_unavailable")
        except ProviderConfigurationError as exc:
            await cleanup()
            return _openai_error(str(exc), 503, "provider_configuration_error")
        except httpx.HTTPError as exc:
            await cleanup()
            return _openai_error(f"upstream request failed: {exc}", 502, "upstream_error")
        except BaseException:
            await cleanup()
            raise

        headers = response_headers(resolved, prepared)
        try:
            if upstream_response.is_error:
                content = await upstream_response.aread()
                status_code = upstream_response.status_code
                content_type = upstream_response.headers.get(
                    "content-type", "application/json"
                )
                await cleanup()
                return Response(
                    content=content,
                    status_code=status_code,
                    media_type=content_type,
                    headers=headers,
                )
            if stream:
                content_type = upstream_response.headers.get(
                    "content-type", "text/event-stream"
                )
                return StreamingResponse(
                    upstream_response.aiter_raw(),
                    status_code=upstream_response.status_code,
                    media_type=content_type,
                    headers=headers,
                    background=BackgroundTask(cleanup),
                )
            content = await upstream_response.aread()
            content_type = upstream_response.headers.get(
                "content-type", "application/json"
            )
            status_code = upstream_response.status_code
            await cleanup()
            return Response(
                content=content,
                status_code=status_code,
                media_type=content_type,
                headers=headers,
            )
        except BaseException:
            await cleanup()
            raise

    def resolve_payload(
        request: Request,
        body: dict[str, Any],
        endpoint: Endpoint,
        default_profile: str,
        messages: list[dict[str, Any]] | None = None,
    ) -> ResolvedProfile:
        requested = body.get("model", default_profile)
        if not isinstance(requested, str):
            raise UnknownModelError("model must be a string")
        return current_registry(request).resolve(
            requested,
            messages or [],
            profile_header(request),
            endpoint,
        )

    async def request_json(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            value = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _openai_error("request body must be valid JSON", 400)
        if not isinstance(value, dict):
            return _openai_error("request body must be a JSON object", 400)
        return value

    @application.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health")
    @application.get("/health/ready")
    async def health_ready(request: Request) -> JSONResponse:
        runtime: Runtime | None = getattr(request.app.state, "runtime", None)
        if runtime is None:
            return JSONResponse(status_code=503, content={"status": "starting"})
        document: RegistryDocument = current_registry(request).document
        checks: dict[str, Any] = {"memory": await runtime.memory.ping()}
        required_failures: list[str] = []
        for provider_id, provider in document.providers.items():
            if not provider.enabled:
                continue
            url = _health_url(provider)
            if url is None:
                checks[f"provider:{provider_id}"] = True
                continue
            try:
                response = await runtime.client.get(url)
                healthy = response.is_success
            except httpx.HTTPError:
                healthy = False
            checks[f"provider:{provider_id}"] = healthy
            if provider.required and not healthy:
                required_failures.append(provider_id)
        memory_ready = bool(checks["memory"]) or not settings.memory_required
        ready = memory_ready and not required_failures
        fully_healthy = all(bool(value) for value in checks.values())
        status = "ok" if fully_healthy else "degraded" if ready else "unavailable"
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": status,
                "checks": checks,
                "required_provider_failures": required_failures,
            },
        )

    @application.get("/metrics")
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @application.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        return {"object": "list", "data": current_registry(request).advertised_models()}

    @application.get("/api/registry")
    async def registry_document(request: Request) -> dict[str, Any]:
        return current_registry(request).document.model_dump(by_alias=True, mode="json")

    @application.get("/api/registry/schema")
    async def registry_schema() -> dict[str, Any]:
        return RegistryDocument.model_json_schema(by_alias=True)

    @application.get("/api/capabilities")
    async def capabilities(request: Request) -> dict[str, Any]:
        return current_registry(request).capability_report()

    @application.post("/api/routes/preview")
    async def route_preview(preview: RoutePreview, request: Request) -> dict[str, Any]:
        try:
            resolved = current_registry(request).resolve(
                preview.model,
                preview.messages,
                preview.profile_override,
                preview.endpoint,
            )
        except (UnknownModelError, UnavailableProfileError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "requested_model": resolved.requested_model,
            "profile": resolved.profile_id,
            "model": resolved.model_id,
            "provider": resolved.provider_id,
            "endpoint": resolved.profile.endpoint,
            "capabilities": resolved.model.capabilities,
            "reason": resolved.route_reason,
        }

    @application.post("/api/admin/reload-registry")
    @application.post("/api/admin/reload-profiles")
    async def reload_registry(request: Request) -> dict[str, Any]:
        document = load_registry(settings.registry_config_path)
        request.app.state.registry = ProfileRegistry(document)
        return {
            "status": "reloaded",
            "providers": sorted(document.providers),
            "models": sorted(document.models),
            "profiles": sorted(document.profiles),
        }

    @application.get("/api/models/status")
    async def model_status(request: Request) -> dict[str, Any]:
        runtime: Runtime = request.app.state.runtime
        result: dict[str, Any] = {"providers": {}}
        for provider_id, provider in current_registry(request).document.providers.items():
            if not provider.enabled:
                result["providers"][provider_id] = {"enabled": False}
                continue
            if provider.type != "llama_cpp":
                result["providers"][provider_id] = {
                    "enabled": True,
                    "type": provider.type,
                    "status": "external",
                }
                continue
            synthetic_model = next(
                (
                    (model_id, model)
                    for model_id, model in current_registry(request).document.models.items()
                    if model.provider == provider_id
                ),
                None,
            )
            if synthetic_model is None:
                result["providers"][provider_id] = {
                    "enabled": True,
                    "type": provider.type,
                    "models": [],
                }
                continue
            model_id, model = synthetic_model
            profile = current_registry(request).resolve(
                model_id, [], endpoint="chat"
            ) if "chat" in model.capabilities else None
            if profile is None:
                result["providers"][provider_id] = {
                    "enabled": True,
                    "type": provider.type,
                    "status": "registered",
                }
                continue
            gate = provider_gate(runtime, profile)
            coordinator = provider_coordinator(runtime, profile, gate)
            try:
                result["providers"][provider_id] = await coordinator.status_payload()
            except (httpx.HTTPError, ModelCoordinationError) as exc:
                result["providers"][provider_id] = {"error": str(exc)}
        return result

    @application.post("/api/memories")
    async def create_memory(memory: MemoryCreate, request: Request) -> JSONResponse:
        runtime: Runtime = request.app.state.runtime
        if not runtime.memory.enabled:
            return JSONResponse(status_code=503, content={"error": "shared memory is disabled"})
        record = await runtime.memory.add(
            user_id=memory.user_id or user_header(request),
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
            user_id=user_header(request),
            namespaces=namespace or ["general", "user", "projects"],
            query=q,
            limit=limit,
        )
        return {"data": [_record_to_dict(record) for record in records]}

    @application.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        body = await request_json(request)
        if isinstance(body, JSONResponse):
            return body
        messages = body.get("messages")
        if not isinstance(messages, list):
            return _openai_error("messages must be an array", 400)
        try:
            resolved = resolve_payload(request, body, "chat", "auto", messages)
        except UnknownModelError as exc:
            return _openai_error(str(exc), 404)
        except UnavailableProfileError as exc:
            return _openai_error(str(exc), 503, "model_unavailable")

        runtime: Runtime = request.app.state.runtime
        memories = []
        if runtime.memory.enabled and resolved.profile.memory.enabled and settings.memory_top_k:
            namespaces = list(resolved.profile.memory.namespaces)
            extra_namespace = namespace_header(request)
            if extra_namespace and extra_namespace not in namespaces:
                namespaces.append(extra_namespace)
            try:
                memories = await runtime.memory.search(
                    user_id=user_header(request),
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
            prepared = prepare_chat_payload(
                body,
                resolved,
                memories,
                settings.memory_max_chars,
                request.headers.get("X-Reasoning-Effort"),
                route_prefixes(current_registry(request).document.routes),
            )
        except InvalidChatRequest as exc:
            return _openai_error(str(exc), 400)
        stream = bool(prepared.payload.get("stream", False))
        return await proxy_request(
            request,
            resolved,
            prepared.payload,
            "chat",
            stream=stream,
            prepared=prepared,
        )

    async def simple_endpoint(
        request: Request,
        endpoint: Endpoint,
        default_profile: str,
    ) -> Response:
        body = await request_json(request)
        if isinstance(body, JSONResponse):
            return body
        try:
            resolved = resolve_payload(request, body, endpoint, default_profile)
        except UnknownModelError as exc:
            return _openai_error(str(exc), 404)
        except UnavailableProfileError as exc:
            return _openai_error(str(exc), 503, "model_unavailable")
        payload = dict(body)
        for key, value in resolved.profile.defaults.items():
            payload.setdefault(key, value)
        payload["model"] = resolved.upstream_model
        return await proxy_request(
            request,
            resolved,
            payload,
            endpoint,
            stream=bool(payload.get("stream", False)),
        )

    @application.post("/v1/completions")
    async def completions(request: Request) -> Response:
        return await simple_endpoint(request, "completion", "completion")

    @application.post("/v1/images/generations")
    async def image_generations(request: Request) -> Response:
        return await simple_endpoint(request, "image", "image")

    @application.post("/v1/embeddings")
    async def embeddings(request: Request) -> Response:
        return await simple_endpoint(request, "embedding", "embedding")

    @application.post("/v1/rerank")
    @application.post("/v1/reranking")
    async def rerank(request: Request) -> Response:
        return await simple_endpoint(request, "rerank", "rerank")

    return application


app = create_app()
