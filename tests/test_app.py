from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi.testclient import TestClient

from local_ai_hub.app import create_app
from local_ai_hub.config import Settings
from local_ai_hub.memory import NullMemoryStore

AUTH = {"Authorization": "Bearer test-key"}


def make_transport(captured: list[dict[str, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        captured.append({"path": request.url.path, "body": body, "headers": dict(request.headers)})
        if request.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"created": 1, "data": [{"url": "local://image"}]})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={"object": "list", "data": [{"index": 0, "embedding": [0.1, 0.2]}]},
            )
        if request.url.path.endswith("/rerank"):
            return httpx.Response(200, json={"results": [{"index": 0, "score": 0.9}]})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            },
        )

    return httpx.MockTransport(handler)


def test_public_health_and_schema(settings: Settings) -> None:
    with TestClient(
        create_app(settings, transport=make_transport([]), memory_store=NullMemoryStore())
    ) as client:
        assert client.get("/health/live").status_code == 200
        schema = client.get("/api/registry/schema")
        assert schema.status_code == 200
        assert "providers" in schema.json()["properties"]


def test_private_routes_require_api_key(settings: Settings) -> None:
    with TestClient(
        create_app(settings, transport=make_transport([]), memory_store=NullMemoryStore())
    ) as client:
        response = client.get("/v1/models")
        assert response.status_code == 401
        assert response.json()["error"]["type"] == "authentication_error"


def test_models_list_only_resolvable_profiles(settings: Settings) -> None:
    with TestClient(
        create_app(settings, transport=make_transport([]), memory_store=NullMemoryStore())
    ) as client:
        response = client.get("/v1/models", headers=AUTH)
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert {"auto", "chat", "code", "story", "vision", "image", "embedding"}.issubset(ids)
        assert "agent" not in ids


def test_auto_story_route_proxies_selected_model(settings: Settings) -> None:
    captured: list[dict[str, Any]] = []
    with TestClient(
        create_app(
            settings,
            transport=make_transport(captured),
            memory_store=NullMemoryStore(),
        )
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "/story Continue the scene"}],
            },
        )
    assert response.status_code == 200
    assert response.headers["X-Agent-UI-Profile"] == "story"
    assert response.headers["X-Agent-UI-Model"] == "story-model"
    assert captured[0]["path"].endswith("/chat/completions")
    assert captured[0]["body"]["model"] == "story-model"
    assert captured[0]["body"]["messages"][-1]["content"] == "Continue the scene"


def test_direct_model_reasoning_effort_is_forwarded(settings: Settings) -> None:
    captured: list[dict[str, Any]] = []
    with TestClient(
        create_app(
            settings,
            transport=make_transport(captured),
            memory_store=NullMemoryStore(),
        )
    ) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "X-Reasoning-Effort": "high"},
            json={
                "model": "general-model",
                "messages": [{"role": "user", "content": "Analyze this"}],
            },
        )
    assert response.status_code == 200
    assert captured[0]["body"]["reasoning_effort"] == "high"
    assert response.headers["X-Agent-UI-Reasoning-Applied"] == "high"


def test_image_generation_uses_registered_image_provider(settings: Settings) -> None:
    captured: list[dict[str, Any]] = []
    with TestClient(
        create_app(
            settings,
            transport=make_transport(captured),
            memory_store=NullMemoryStore(),
        )
    ) as client:
        response = client.post(
            "/v1/images/generations",
            headers=AUTH,
            json={"model": "image", "prompt": "a quiet observatory"},
        )
    assert response.status_code == 200
    assert response.json()["data"][0]["url"] == "local://image"
    assert captured[0]["body"]["model"] == "diffusion-upstream"
    assert response.headers["X-Agent-UI-Model"] == "image-model"


def test_embedding_and_rerank_endpoints(settings: Settings) -> None:
    captured: list[dict[str, Any]] = []
    with TestClient(
        create_app(
            settings,
            transport=make_transport(captured),
            memory_store=NullMemoryStore(),
        )
    ) as client:
        embedding = client.post(
            "/v1/embeddings",
            headers=AUTH,
            json={"model": "embedding", "input": "hello"},
        )
        rerank = client.post(
            "/v1/rerank",
            headers=AUTH,
            json={"model": "rerank", "query": "hello", "documents": ["hello"]},
        )
    assert embedding.status_code == 200
    assert rerank.status_code == 200
    assert [item["path"] for item in captured] == ["/v1/embeddings", "/v1/rerank"]


def test_capabilities_expose_available_and_unavailable_profiles(settings: Settings) -> None:
    with TestClient(
        create_app(settings, transport=make_transport([]), memory_store=NullMemoryStore())
    ) as client:
        response = client.get("/api/capabilities", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["profiles"]["image"]["available"] is True
    assert response.json()["models"]["general-model"]["features"]["reasoning"]["transport"] == "flat"


def test_missing_capability_returns_actionable_503(
    tmp_path: Path,
    registry_data: dict[str, Any],
) -> None:
    data = deepcopy(registry_data)
    data["models"].pop("image-model")
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    settings = Settings(
        registry_config_path=registry_path,
        gateway_api_key="test-key",
        memory_enabled=False,
    )
    with TestClient(
        create_app(settings, transport=make_transport([]), memory_store=NullMemoryStore())
    ) as client:
        response = client.post(
            "/v1/images/generations",
            headers=AUTH,
            json={"model": "image", "prompt": "test"},
        )
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "model_unavailable"
    assert "capabilities" in response.json()["error"]["message"]


def test_registry_reload_accepts_new_model(
    settings: Settings,
    registry_path: Path,
    registry_data: dict[str, Any],
) -> None:
    with TestClient(
        create_app(settings, transport=make_transport([]), memory_store=NullMemoryStore())
    ) as client:
        data = deepcopy(registry_data)
        data["models"]["second-chat"] = {
            "provider": "mock",
            "priority": 999,
            "capabilities": ["chat"],
        }
        registry_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        reloaded = client.post("/api/admin/reload-registry", headers=AUTH)
        preview = client.post(
            "/api/routes/preview",
            headers=AUTH,
            json={"model": "chat", "messages": [{"role": "user", "content": "hello"}]},
        )
    assert reloaded.status_code == 200
    assert preview.status_code == 200
    assert preview.json()["model"] == "second-chat"
