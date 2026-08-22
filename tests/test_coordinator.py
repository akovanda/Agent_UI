from __future__ import annotations

import asyncio

import httpx
import pytest

from local_ai_hub.coordinator import LlamaModelCoordinator, ModelCoordinationError


@pytest.mark.asyncio
async def test_explicit_coordinator_unloads_then_loads() -> None:
    state = {"gpt-oss-20b": "loaded", "stheno-8b": "unloaded"}
    actions: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": model, "status": {"value": status}}
                        for model, status in state.items()
                    ]
                },
            )
        body = __import__("json").loads(request.content)
        model = body["model"]
        if request.url.path == "/models/unload":
            actions.append(("unload", model))
            state[model] = "unloaded"
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/models/load":
            actions.append(("load", model))
            state[model] = "loaded"
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = LlamaModelCoordinator(
            client,
            "http://llama",
            "explicit",
            timeout_seconds=1,
            poll_interval_seconds=0.001,
            transition_lock=asyncio.Lock(),
        )
        await coordinator.ensure_loaded("stheno-8b")

    assert actions == [("unload", "gpt-oss-20b"), ("load", "stheno-8b")]


@pytest.mark.asyncio
async def test_missing_model_is_reported() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = LlamaModelCoordinator(
            client,
            "http://llama",
            "explicit",
            timeout_seconds=1,
            poll_interval_seconds=0.001,
            transition_lock=asyncio.Lock(),
        )
        with pytest.raises(ModelCoordinationError, match="does not advertise"):
            await coordinator.ensure_loaded("missing")


@pytest.mark.asyncio
async def test_noop_and_already_loaded_paths() -> None:
    called = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(
            200, json={"data": [{"id": "gpt-oss-20b", "status": {"value": "loaded"}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        noop = LlamaModelCoordinator(client, "http://llama", "none", 1, 0.001, asyncio.Lock())
        await noop.ensure_loaded("anything")
        assert called == 0
        explicit = LlamaModelCoordinator(
            client, "http://llama", "explicit", 1, 0.001, asyncio.Lock()
        )
        await explicit.ensure_loaded("gpt-oss-20b")
        payload = await explicit.status_payload()
    assert payload["models"][0]["status"] == "loaded"


@pytest.mark.asyncio
async def test_existing_loading_target_is_awaited_without_duplicate_load() -> None:
    states = iter(["loading", "loading", "loaded"])
    posts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/models":
            try:
                value = next(states)
            except StopIteration:
                value = "loaded"
            return httpx.Response(
                200,
                json={"data": [{"id": "gpt-oss-20b", "status": {"value": value}}]},
            )
        posts.append(request.url.path)
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = LlamaModelCoordinator(
            client, "http://llama", "explicit", 1, 0.001, asyncio.Lock()
        )
        await coordinator.ensure_loaded("gpt-oss-20b")

    assert posts == []


@pytest.mark.asyncio
async def test_malformed_model_listing_is_reported() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": "not-an-array"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        coordinator = LlamaModelCoordinator(
            client, "http://llama", "explicit", 1, 0.001, asyncio.Lock()
        )
        with pytest.raises(ModelCoordinationError, match="data array"):
            await coordinator.list_models()
