from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from local_ai_hub.app import create_app
from local_ai_hub.config import Settings
from local_ai_hub.memory import NullMemoryStore


def write_catalog(path: Path, models: str, experiences: str) -> None:
    path.write_text(
        f"""
version: 2
backends:
  endpoint:
    kind: openai-compatible
    base_url: http://backend/v1
    api_key_env: TEST_BACKEND_KEY
    coordinator: none
    serialize_requests: false
models:
{models}
experiences:
{experiences}
""".lstrip(),
        encoding="utf-8",
    )


def settings(path: Path) -> Settings:
    return Settings(
        profile_config_path=path,
        gateway_api_key=SecretStr("gateway-test-key"),
        memory_enabled=False,
        model_coordinator_mode="none",
    )


def auth_headers(**extra: str) -> dict[str, str]:
    return {"Authorization": "Bearer gateway-test-key", **extra}


def test_empty_install_is_healthy_and_explains_setup(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text(
        """
version: 2
backends: {}
models: {}
experiences:
  chat: {capability: chat, description: General chat.}
""".lstrip(),
        encoding="utf-8",
    )
    application = create_app(settings(path), memory_store=NullMemoryStore())
    with TestClient(application) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "setup_required"
        status = client.get("/api/setup/status", headers=auth_headers())
        assert status.status_code == 200
        assert status.json()["setup_required"] is True
        listed = client.get("/v1/models", headers=auth_headers()).json()["data"]
        assert listed[0]["id"] == "chat"
        assert listed[0]["metadata"]["available"] is False


def test_chat_capability_selects_priority_and_maps_reasoning(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_BACKEND_KEY", "backend-key")
    path = tmp_path / "catalog.yaml"
    write_catalog(
        path,
        models="""  lower:
    backend: endpoint
    upstream_model: lower-upstream
    priority: 10
    capabilities: [chat]
    artifact: {kind: none}
  preferred:
    backend: endpoint
    upstream_model: preferred-upstream
    priority: 100
    capabilities: {chat: {}, code: {}}
    features:
      reasoning:
        request_field: effort
        transport: body
        values: {fast: low, deep: high}
        unsupported_policy: reject
    artifact: {kind: none}
""",
        experiences="""  chat:
    capability: chat
    system_prompt: Answer accurately.
    defaults: {temperature: 0.5}
""",
    )
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "path": request.url.path,
                "authorization": request.headers.get("authorization"),
                "json": json.loads(request.content),
            }
        )
        return httpx.Response(
            200,
            json={"id": "response", "choices": [{"message": {"content": "ok"}}]},
            headers={"content-type": "application/json"},
        )

    application = create_app(
        settings(path),
        transport=httpx.MockTransport(handler),
        memory_store=NullMemoryStore(),
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(**{"X-Reasoning-Effort": "deep"}),
            json={"model": "chat", "messages": [{"role": "user", "content": "Hello"}]},
        )
    assert response.status_code == 200
    assert response.headers["x-agent-ui-model"] == "preferred"
    assert response.headers["x-agent-ui-backend"] == "endpoint"
    assert captured[0]["path"] == "/v1/chat/completions"
    assert captured[0]["authorization"] == "Bearer backend-key"
    assert captured[0]["json"]["model"] == "preferred-upstream"
    assert captured[0]["json"]["effort"] == "high"
    assert captured[0]["json"]["temperature"] == 0.5
    assert captured[0]["json"]["messages"][0]["role"] == "system"


def test_image_embedding_and_infill_paths_are_capability_gated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.yaml"
    write_catalog(
        path,
        models="""  multi:
    backend: endpoint
    upstream_model: multi-upstream
    priority: 50
    capabilities: {code: {}, image: {}, embeddings: {}}
    artifact: {kind: none}
""",
        experiences="""  code: {capability: code}
  image: {capability: image}
  embeddings: {capability: embeddings, advertised: false}
""",
    )
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        payload = json.loads(request.content)
        if request.url.path.endswith("images/generations"):
            return httpx.Response(200, json={"data": [{"b64_json": "AA=="}]})
        if request.url.path.endswith("embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.0]}]})
        return httpx.Response(200, json={"content": "completion", "model": payload["model"]})

    application = create_app(
        settings(path),
        transport=httpx.MockTransport(handler),
        memory_store=NullMemoryStore(),
    )
    with TestClient(application) as client:
        infill = client.post(
            "/v1/infill",
            headers=auth_headers(),
            json={"model": "code", "input_prefix": "def f():", "input_suffix": ""},
        )
        image = client.post(
            "/v1/images/generations",
            headers=auth_headers(),
            json={"model": "image", "prompt": "a diagram"},
        )
        embeddings = client.post(
            "/v1/embeddings",
            headers=auth_headers(),
            json={"model": "embeddings", "input": "hello"},
        )
        rejected = client.post(
            "/v1/rerank",
            headers=auth_headers(),
            json={"model": "multi", "query": "q", "documents": ["a"]},
        )

    assert infill.status_code == 200
    assert image.status_code == 200
    assert embeddings.status_code == 200
    assert rejected.status_code == 400
    assert paths == ["/infill", "/v1/images/generations", "/v1/embeddings"]


def test_unknown_reasoning_value_is_rejected_before_upstream(tmp_path: Path) -> None:
    path = tmp_path / "catalog.yaml"
    write_catalog(
        path,
        models="""  text:
    backend: endpoint
    capabilities: [chat]
    features:
      reasoning:
        values: {fast: low, deep: high}
        unsupported_policy: reject
    artifact: {kind: none}
""",
        experiences="""  chat: {capability: chat}
""",
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    application = create_app(
        settings(path),
        transport=httpx.MockTransport(handler),
        memory_store=NullMemoryStore(),
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=auth_headers(**{"X-Reasoning-Effort": "invented"}),
            json={"model": "chat", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 400
    assert calls == 0
