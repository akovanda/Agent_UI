from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Backend, ProfileDocument, Settings
from .coordinator import LlamaModelCoordinator
from .gpu import GpuGate

_DEFAULT_ENDPOINTS = {
    "chat": "/v1/chat/completions",
    "responses": "/v1/responses",
    "completions": "/v1/completions",
    "infill": "/infill",
    "embeddings": "/v1/embeddings",
    "rerank": "/v1/rerank",
    "image": "/v1/images/generations",
}


@dataclass(slots=True)
class BackendRuntime:
    backend_id: str
    spec: Backend
    client: httpx.AsyncClient
    base_url: str
    coordinator: LlamaModelCoordinator | None
    gate: GpuGate | None

    def url_for(self, operation: str) -> str:
        endpoint = self.spec.endpoints.get(operation) or _DEFAULT_ENDPOINTS[operation]
        base = self.base_url.rstrip("/")
        if endpoint.startswith("/"):
            return str(httpx.URL(base).copy_with(path=endpoint))
        return f"{base}/{endpoint.lstrip('/')}"

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        if self.gate is None:
            yield
            return
        async with self.gate.lease():
            yield

    async def ensure_loaded(self, upstream_model: str) -> None:
        if self.coordinator is not None:
            await self.coordinator.ensure_loaded(upstream_model)

    async def request(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        stream: bool,
    ) -> httpx.Response:
        request = self.client.build_request(
            "POST",
            self.url_for(operation),
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        return await self.client.send(request, stream=stream)


class BackendRuntimeRegistry:
    def __init__(self, runtimes: dict[str, BackendRuntime], unavailable: dict[str, str]):
        self.runtimes = runtimes
        self.unavailable = unavailable

    @classmethod
    def build(
        cls,
        settings: Settings,
        document: ProfileDocument,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> BackendRuntimeRegistry:
        runtimes: dict[str, BackendRuntime] = {}
        unavailable: dict[str, str] = {}
        timeout = httpx.Timeout(
            connect=settings.upstream_connect_timeout_seconds,
            read=None,
            write=settings.upstream_write_timeout_seconds,
            pool=60.0,
        )
        for backend_id, spec in document.backends.items():
            if not spec.enabled:
                unavailable[backend_id] = "disabled"
                continue
            base_url = spec.resolved_base_url
            if spec.options.get("legacy") and spec.kind == "llama.cpp":
                base_url = settings.llama_base_url
                spec.coordinator = settings.model_coordinator_mode
            if not base_url:
                unavailable[backend_id] = "base URL is not configured"
                continue
            api_key = spec.resolved_api_key
            if spec.kind == "llama.cpp" and not api_key:
                api_key = settings.llama_api_key.get_secret_value()
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            client = httpx.AsyncClient(
                timeout=timeout,
                transport=transport,
                headers=headers,
            )
            gate = GpuGate(1) if spec.serialize_requests else None
            coordinator = None
            if spec.kind == "llama.cpp" and spec.coordinator != "none":
                coordinator = LlamaModelCoordinator(
                    client=client,
                    base_url=base_url.rstrip("/"),
                    mode=spec.coordinator,
                    timeout_seconds=settings.model_load_timeout_seconds,
                    poll_interval_seconds=settings.model_poll_interval_seconds,
                    transition_lock=gate.transition_lock if gate else None,
                )
            runtimes[backend_id] = BackendRuntime(
                backend_id=backend_id,
                spec=spec,
                client=client,
                base_url=base_url,
                coordinator=coordinator,
                gate=gate,
            )
        return cls(runtimes, unavailable)

    def get(self, backend_id: str) -> BackendRuntime:
        try:
            return self.runtimes[backend_id]
        except KeyError as exc:
            reason = self.unavailable.get(backend_id, "not registered")
            raise RuntimeError(f"backend {backend_id!r} is unavailable: {reason}") from exc

    async def close(self) -> None:
        for runtime in self.runtimes.values():
            await runtime.client.aclose()

    async def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for backend_id, runtime in self.runtimes.items():
            healthy = False
            detail: Any = None
            try:
                if runtime.spec.kind == "llama.cpp":
                    response = await runtime.client.get(f"{runtime.base_url.rstrip('/')}/health")
                    healthy = response.is_success
                    detail = response.status_code
                else:
                    response = await runtime.client.get(f"{runtime.base_url.rstrip('/')}/models")
                    healthy = response.is_success
                    detail = response.status_code
            except httpx.HTTPError as exc:
                detail = str(exc)
            result[backend_id] = {
                "kind": runtime.spec.kind,
                "healthy": healthy,
                "detail": detail,
            }
        for backend_id, reason in self.unavailable.items():
            result[backend_id] = {"healthy": False, "detail": reason}
        return result
