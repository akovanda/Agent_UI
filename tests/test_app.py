from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from conftest import FakeMemoryStore, make_settings
from local_ai_hub.app import create_app
from local_ai_hub.memory import MemoryRecord

AUTH = {"Authorization": "Bearer test-secret-key"}


def make_transport(captured: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            captured.append(body)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-local",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                    "model": body["model"],
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_authentication_and_model_listing() -> None:
    app = create_app(make_settings(), transport=make_transport([]), memory_store=FakeMemoryStore())
    with TestClient(app) as client:
        assert client.get("/v1/models").status_code == 401
        response = client.get("/v1/models", headers=AUTH)
    assert response.status_code == 200
    assert "assistant" in {item["id"] for item in response.json()["data"]}


def test_auto_route_uses_story_backend_and_returns_route_headers() -> None:
    captured: list[dict] = []
    app = create_app(
        make_settings(), transport=make_transport(captured), memory_store=FakeMemoryStore()
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Write a scene for our campaign."}],
            },
        )
    assert response.status_code == 200
    assert response.headers["X-Local-AI-Profile"] == "storyteller"
    assert response.headers["X-Local-AI-Backend-Model"] == "stheno-8b"
    assert captured[0]["model"] == "stheno-8b"
    assert captured[0]["temperature"] == 1.3


def test_memory_is_injected_as_untrusted_context() -> None:
    now = datetime.now(UTC)
    record = MemoryRecord(
        id=uuid4(),
        user_id="andrew",
        namespace="infrastructure",
        content="The UCS host has a Tesla T4.",
        source="manual",
        metadata={},
        importance=0.8,
        created_at=now,
        updated_at=now,
    )
    captured: list[dict] = []
    app = create_app(
        make_settings(),
        transport=make_transport(captured),
        memory_store=FakeMemoryStore([record]),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "X-Agent-UI-User": "andrew"},
            json={
                "model": "assistant",
                "messages": [{"role": "user", "content": "What GPU is in the host?"}],
            },
        )
    assert response.status_code == 200
    instruction_text = "\n".join(
        message["content"]
        for message in captured[0]["messages"]
        if message["role"] in {"system", "developer"}
    )
    assert "Tesla T4" in instruction_text
    assert "untrusted reference data" in instruction_text


def test_memory_create_and_search_endpoints() -> None:
    memory = FakeMemoryStore()
    app = create_app(make_settings(), transport=make_transport([]), memory_store=memory)
    with TestClient(app) as client:
        created = client.post(
            "/api/memories",
            headers=AUTH,
            json={"namespace": "projects", "content": "Local AI Hub uses a T4."},
        )
        found = client.get(
            "/api/memories/search",
            headers=AUTH,
            params=[("q", "T4"), ("namespace", "projects")],
        )
    assert created.status_code == 201
    assert found.status_code == 200
    assert found.json()["data"][0]["content"] == "Local AI Hub uses a T4."


def test_invalid_model_returns_openai_error_shape() -> None:
    app = create_app(make_settings(), transport=make_transport([]), memory_store=FakeMemoryStore())
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "does-not-exist", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_public_health_metrics_preview_reload_and_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/models":
            return httpx.Response(
                200,
                json={"data": [{"id": "gpt-oss-20b", "status": {"value": "unloaded"}}]},
            )
        return httpx.Response(404)

    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(handler),
        memory_store=FakeMemoryStore(),
    )
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health").status_code == 200
        assert client.get("/health/ready").status_code == 200
        assert client.get("/metrics").status_code == 200
        preview = client.post(
            "/api/routes/preview",
            headers=AUTH,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Continue the campaign scene."}],
            },
        )
        reloaded = client.post("/api/admin/reload-profiles", headers=AUTH)
        status = client.get("/api/models/status", headers=AUTH)
    assert preview.json()["backend_model"] == "stheno-8b"
    assert reloaded.json()["status"] == "reloaded"
    assert status.json()["backends"]["local-llama"]["healthy"] is True


def test_bad_chat_requests_are_rejected() -> None:
    app = create_app(make_settings(), transport=make_transport([]), memory_store=FakeMemoryStore())
    with TestClient(app) as client:
        invalid_json = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "Content-Type": "application/json"},
            content="{not-json",
        )
        non_object = client.post("/v1/chat/completions", headers=AUTH, json=["bad"])
        bad_model = client.post(
            "/v1/chat/completions", headers=AUTH, json={"model": 3, "messages": []}
        )
        bad_messages = client.post(
            "/v1/chat/completions", headers=AUTH, json={"model": "assistant"}
        )
        bad_effort = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "X-Reasoning-Effort": "extreme"},
            json={"model": "assistant", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert [
        invalid_json.status_code,
        non_object.status_code,
        bad_model.status_code,
        bad_messages.status_code,
        bad_effort.status_code,
    ] == [400, 400, 400, 400, 400]


def test_upstream_error_and_connect_failure_are_mapped() -> None:
    def error_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(500, json={"error": {"message": "backend failed"}})
        return httpx.Response(200, json={"status": "ok"})

    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(error_handler),
        memory_store=FakeMemoryStore(),
    )
    with TestClient(app) as client:
        backend_error = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "assistant", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert backend_error.status_code == 500

    def connect_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200)

    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(connect_handler),
        memory_store=FakeMemoryStore(),
    )
    with TestClient(app) as client:
        unavailable = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "assistant", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert unavailable.status_code == 502
    assert unavailable.json()["error"]["type"] == "upstream_error"


def test_model_coordination_failure_is_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    app = create_app(
        make_settings(model_coordinator_mode="explicit"),
        transport=httpx.MockTransport(handler),
        memory_store=FakeMemoryStore(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "assistant", "messages": [{"role": "user", "content": "Hi"}]},
        )
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "model_unavailable"


def test_streaming_response_is_proxied() -> None:
    class EventStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[]}\n\n'
            yield b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(
                200,
                stream=EventStream(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200)

    app = create_app(
        make_settings(), transport=httpx.MockTransport(handler), memory_store=FakeMemoryStore()
    )
    with (
        TestClient(app) as client,
        client.stream(
            "POST",
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "assistant",
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        ) as response,
    ):
        body = b"".join(response.iter_bytes())
    assert response.status_code == 200
    assert b"[DONE]" in body
    assert response.headers["X-Local-AI-Backend-Model"] == "gpt-oss-20b"


def test_disabled_memory_endpoint_is_explicit() -> None:
    from local_ai_hub.memory import NullMemoryStore

    app = create_app(make_settings(), transport=make_transport([]), memory_store=NullMemoryStore())
    with TestClient(app) as client:
        response = client.post(
            "/api/memories",
            headers=AUTH,
            json={"namespace": "general", "content": "Not stored"},
        )
    assert response.status_code == 503


def test_unexpected_upstream_failure_releases_gpu_lease() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/chat/completions":
            calls += 1
            if calls == 1:
                raise RuntimeError("unexpected transport failure")
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-recovered",
                    "object": "chat.completion",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "recovered"}}
                    ],
                },
            )
        return httpx.Response(404)

    app = create_app(
        make_settings(),
        transport=httpx.MockTransport(handler),
        memory_store=FakeMemoryStore(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        failed = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "assistant", "messages": [{"role": "user", "content": "Hi"}]},
        )
        assert failed.status_code == 502
        backend = app.state.runtime.backends.get("local-llama")
        assert backend.gate is not None
        assert backend.gate._semaphore._value == 1

        recovered = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "assistant", "messages": [{"role": "user", "content": "Again"}]},
        )

    assert recovered.status_code == 200
    assert recovered.json()["choices"][0]["message"]["content"] == "recovered"


def test_response_close_failure_still_releases_gpu_lease(monkeypatch) -> None:
    class FakeResponse:
        is_error = False
        status_code = 200

        def __init__(self, fail_close: bool):
            self.fail_close = fail_close
            self.headers = {"content-type": "application/json"}

        async def aread(self) -> bytes:
            return json.dumps(
                {
                    "id": "chatcmpl-close-test",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                }
            ).encode()

        async def aclose(self) -> None:
            if self.fail_close:
                raise RuntimeError("close failed")

    app = create_app(make_settings(), transport=make_transport([]), memory_store=FakeMemoryStore())
    with TestClient(app, raise_server_exceptions=False) as client:
        calls = 0

        async def fake_request(_self, _operation, _payload, *, stream):
            nonlocal calls
            calls += 1
            assert stream is False
            return FakeResponse(fail_close=calls == 1)

        backend = app.state.runtime.backends.get("local-llama")
        monkeypatch.setattr(type(backend), "request", fake_request)
        failed = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "assistant", "messages": [{"role": "user", "content": "Hi"}]},
        )
        assert failed.status_code == 500
        assert backend.gate is not None
        assert backend.gate._semaphore._value == 1

        recovered = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "assistant",
                "messages": [{"role": "user", "content": "Again"}],
            },
        )

    assert recovered.status_code == 200
    assert recovered.json()["choices"][0]["message"]["content"] == "ok"


def test_optional_memory_failure_is_degraded_but_ready() -> None:
    class UnavailableMemory(FakeMemoryStore):
        async def ping(self) -> bool:
            return False

    app = create_app(
        make_settings(memory_required=False),
        transport=make_transport([]),
        memory_store=UnavailableMemory(),
    )
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["memory"] is False
    assert body["checks"]["backends"]["local-llama"]["healthy"] is True


def test_required_memory_failure_marks_gateway_unready() -> None:
    class UnavailableMemory(FakeMemoryStore):
        async def ping(self) -> bool:
            return False

    app = create_app(
        make_settings(memory_required=True),
        transport=make_transport([]),
        memory_store=UnavailableMemory(),
    )
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_fixed_model_fallback_rejects_wrong_profile() -> None:
    captured: list[dict] = []
    app = create_app(
        make_settings(model_coordinator_mode="none", fixed_backend_model="gpt-oss-20b"),
        transport=make_transport(captured),
        memory_store=FakeMemoryStore(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "storyteller",
                "messages": [{"role": "user", "content": "Continue the scene."}],
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "model_unavailable"
    assert "fixed-model fallback" in response.json()["error"]["message"]
    assert captured == []


def test_llama_api_key_is_forwarded_to_router_requests() -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/chat/completions":
            observed.append(request.headers.get("authorization", ""))
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-auth",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                    "model": body["model"],
                },
            )
        return httpx.Response(404)

    app = create_app(
        make_settings(llama_api_key="router-secret"),
        transport=httpx.MockTransport(handler),
        memory_store=FakeMemoryStore(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "assistant", "messages": [{"role": "user", "content": "Hi"}]},
        )

    assert response.status_code == 200
    assert observed == ["Bearer router-secret"]
