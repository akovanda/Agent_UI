from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import FakeMemoryStore, make_settings
from local_ai_hub.app import create_app
from local_ai_hub.memory_config import MemoryConfig
from local_ai_hub.memory_provider import (
    BuiltinPostgresProvider,
    ContinuityHttpProvider,
    MemoryScope,
)
from local_ai_hub.memory_repository import InMemoryMemoryRepository
from local_ai_hub.memory_service import MemoryService

AUTH = {"Authorization": "Bearer test-secret-key"}


def _jwt(subject: str, secret: str, **claims) -> str:
    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = encode({"alg": "HS256", "typ": "JWT"})
    payload = encode({"sub": subject, "exp": int(time.time()) + 300, **claims})
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _headers(subject: str, secret: str) -> dict[str, str]:
    return {**AUTH, "X-OpenWebUI-User-Jwt": _jwt(subject, secret)}


def _model_transport(captured: list[dict] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/health", "/models"}:
            return httpx.Response(200, json={"status": "ok", "data": []})
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            if captured is not None:
                captured.append(body)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-memory",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                    "model": body["model"],
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _wait_for_proposals(client: TestClient, headers: dict[str, str], count: int) -> list[dict]:
    for _ in range(100):
        response = client.get("/api/memory/v1/proposals", headers=headers)
        if response.status_code == 200 and len(response.json()["data"]) >= count:
            return response.json()["data"]
        time.sleep(0.01)
    raise AssertionError("proposal extraction did not finish")


def _wait_for_extractions(calls: list[str], count: int) -> None:
    for _ in range(100):
        if len(calls) >= count:
            return
        time.sleep(0.01)
    raise AssertionError("memory extraction did not finish")


def test_signed_identity_isolates_users_and_unsigned_spoofing_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "webui-test-secret"
    monkeypatch.setenv("WEBUI_SECRET_KEY", secret)
    monkeypatch.setenv("MEMORY_SUBJECT_HMAC_KEY", "subject-test-secret")
    memory = FakeMemoryStore()
    app = create_app(make_settings(), transport=_model_transport(), memory_store=memory)
    with TestClient(app) as client:
        created = client.post(
            "/api/memories",
            headers=_headers("user-a", secret),
            json={"namespace": "general", "content": "User A likes cedar tea."},
        )
        own = client.get("/api/memory/v1/records", headers=_headers("user-a", secret))
        other = client.get("/api/memory/v1/records", headers=_headers("user-b", secret))
        spoofed = client.get(
            "/api/memory/v1/records",
            headers={**AUTH, "X-Agent-UI-User": "user-a"},
        )
        bad_jwt = client.get(
            "/api/memory/v1/records",
            headers={**AUTH, "X-OpenWebUI-User-Jwt": _jwt("user-a", "wrong-secret")},
        )

    assert created.status_code == 201
    assert [item["content"] for item in own.json()["data"]] == ["User A likes cedar tea."]
    assert other.json()["data"] == []
    assert spoofed.status_code == 401
    assert bad_jwt.status_code == 401


def test_browser_cookie_auth_and_csrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "webui-browser-secret"
    monkeypatch.setenv("WEBUI_SECRET_KEY", secret)
    app = create_app(make_settings(), transport=_model_transport(), memory_store=FakeMemoryStore())
    with TestClient(app) as client:
        client.cookies.set("token", _jwt("browser-user", secret))
        page = client.get("/memory")
        denied = client.patch(
            "/api/memory/v1/settings",
            json={"enabled": False, "capture_enabled": False, "retrieval_enabled": False},
        )
        allowed = client.patch(
            "/api/memory/v1/settings",
            headers={"X-Agent-UI-CSRF": "1"},
            json={"enabled": False, "capture_enabled": False, "retrieval_enabled": False},
        )

    assert page.status_code == 200
    assert "Nothing is remembered until you approve it" in page.text
    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_personal_memory_crosses_chat_experiences_but_not_story(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_SUBJECT_HMAC_KEY", "subject-test-secret")
    captured: list[dict] = []
    app = create_app(
        make_settings(), transport=_model_transport(captured), memory_store=FakeMemoryStore()
    )
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/memories",
                headers=AUTH,
                json={"namespace": "projects", "content": "The project codename is Juniper."},
            ).status_code
            == 201
        )
        assistant = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "assistant",
                "messages": [{"role": "user", "content": "What is the codename?"}],
            },
        )
        story = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "storyteller",
                "messages": [{"role": "user", "content": "Continue the campaign."}],
            },
        )

    assert assistant.status_code == 200
    assert story.status_code == 200
    assistant_context = json.dumps(captured[0]["messages"])
    story_context = json.dumps(captured[1]["messages"])
    assert "Juniper" in assistant_context
    assert "Juniper" not in story_context


def test_shipped_config_keeps_automatic_memory_off() -> None:
    captured: list[dict] = []
    app = create_app(
        make_settings(memory_config_path=Path("config/memory/base.yaml")),
        transport=_model_transport(captured),
        memory_store=FakeMemoryStore(),
    )
    with TestClient(app) as client:
        client.post(
            "/api/memories",
            headers=AUTH,
            json={"namespace": "general", "content": "Manual memory remains available."},
        )
        chat = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "assistant", "messages": [{"role": "user", "content": "Recall?"}]},
        )
        status = client.get("/api/memory/v1/status", headers=AUTH)

    assert chat.status_code == 200
    assert "Manual memory remains available" not in json.dumps(captured[0]["messages"])
    assert status.json()["automatic"]["operator_enabled"] is False
    assert status.json()["automatic"]["capture_mode"] == "review"
    assert status.json()["user"]["enabled"] is False


def test_legacy_migration_excludes_story_and_export_import_is_idempotent() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from local_ai_hub.memory import MemoryRecord

    now = datetime.now(UTC)
    source_store = FakeMemoryStore(
        [
            MemoryRecord(
                uuid4(),
                "local-user",
                "general",
                "Personal legacy fact.",
                "legacy",
                {},
                0.5,
                now,
                now,
            ),
            MemoryRecord(
                uuid4(),
                "local-user",
                "story",
                "Story-only dragon fact.",
                "legacy",
                {},
                0.5,
                now,
                now,
            ),
        ]
    )
    source_app = create_app(
        make_settings(), transport=_model_transport(), memory_store=source_store
    )
    with TestClient(source_app) as client:
        records = client.get("/api/memory/v1/records", headers=AUTH)
        exported = client.get("/api/memory/v1/export", headers=AUTH)

    destination_app = create_app(
        make_settings(), transport=_model_transport(), memory_store=FakeMemoryStore()
    )
    with TestClient(destination_app) as client:
        first = client.post("/api/memory/v1/import", headers=AUTH, json=exported.json())
        second = client.post("/api/memory/v1/import", headers=AUTH, json=exported.json())
        imported = client.get("/api/memory/v1/records", headers=AUTH)

    assert [item["content"] for item in records.json()["data"]] == ["Personal legacy fact."]
    assert any(record.namespace == "story" for record in source_store.records)
    assert first.json() == {"imported": 1, "skipped": 0}
    assert second.json() == {"imported": 0, "skipped": 1}
    assert [item["content"] for item in imported.json()["data"]] == ["Personal legacy fact."]


def test_async_capture_review_lifecycle_and_secret_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_SUBJECT_HMAC_KEY", "subject-test-secret")
    extracted_text: list[str] = []

    async def extractor(user_text: str) -> list[dict]:
        extracted_text.append(user_text)
        if "credential" in user_text:
            return [{"content": "api_key=sk_12345678901234567890", "importance": 1.0}]
        return [{"content": "The user prefers concise release notes.", "importance": 0.8}]

    app = create_app(
        make_settings(memory_config_path=Path("tests/fixtures/memory-capture.yaml")),
        transport=_model_transport(),
        memory_store=FakeMemoryStore(),
        proposal_extractor=extractor,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "X-OpenWebUI-Chat-Id": "chat-one"},
            json={
                "model": "assistant",
                "messages": [
                    {"role": "system", "content": "Never capture this."},
                    {"role": "assistant", "content": "Nor this."},
                    {"role": "tool", "content": "Tool secret."},
                    {"role": "user", "content": "I prefer concise release notes."},
                ],
            },
        )
        proposals = _wait_for_proposals(client, AUTH, 1)
        proposal_id = proposals[0]["id"]
        before = client.get("/api/memory/v1/records", headers=AUTH)
        approved = client.post(
            f"/api/memory/v1/proposals/{proposal_id}/approve", headers=AUTH, json={}
        )
        records = client.get("/api/memory/v1/records", headers=AUTH).json()["data"]
        reference_id = records[0]["id"]
        corrected = client.patch(
            f"/api/memory/v1/records/{reference_id}",
            headers=AUTH,
            json={"content": "The user prefers short release notes.", "reason": "wording"},
        )
        forgotten = client.post(
            f"/api/memory/v1/records/{reference_id}/forget",
            headers=AUTH,
            json={"reason": "not useful"},
        )
        purged = client.request(
            "DELETE",
            f"/api/memory/v1/records/{reference_id}",
            headers=AUTH,
            json={"reason": "remove permanently"},
        )
        after_purge = client.get("/api/memory/v1/records", headers=AUTH)

        client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "assistant",
                "messages": [{"role": "user", "content": "credential candidate"}],
            },
        )
        time.sleep(0.05)
        pending_after_secret = client.get("/api/memory/v1/proposals", headers=AUTH).json()["data"]

    assert response.status_code == 200
    assert app.state.runtime.memory_service.config.automatic.capture_mode == "review"
    assert extracted_text[0] == "I prefer concise release notes."
    assert before.json()["data"] == []
    assert approved.status_code == 200
    assert corrected.json()["content"] == "The user prefers short release notes."
    assert forgotten.json()["status"] == "forgotten"
    assert purged.json()["status"] == "purged"
    assert after_purge.json()["data"] == []
    assert pending_after_secret == []
    repository = app.state.runtime.memory_service.repository
    assert all("content" not in event["metadata"] for event in repository.audit_events)


def test_automatic_capture_persists_safe_candidates_idempotently_but_excludes_story(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MEMORY_SUBJECT_HMAC_KEY", "subject-test-secret")
    overlay = tmp_path / "automatic-capture.yaml"
    overlay.write_text("automatic:\n  capture_mode: automatic\n", encoding="utf-8")
    extracted_text: list[str] = []

    async def extractor(user_text: str) -> list[dict]:
        extracted_text.append(user_text)
        if "credential" in user_text:
            return [{"content": "api_key=sk_12345678901234567890", "importance": 1.0}]
        return [{"content": "The user prefers concise release notes.", "importance": 0.8}]

    memory = FakeMemoryStore()
    app = create_app(
        make_settings(
            memory_config_path=Path("tests/fixtures/memory-capture.yaml"),
            memory_config_overlay_path=overlay,
        ),
        transport=_model_transport(),
        memory_store=memory,
        proposal_extractor=extractor,
    )
    with TestClient(app) as client:
        request = {
            "model": "assistant",
            "messages": [{"role": "user", "content": "I prefer concise release notes."}],
        }
        first = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "X-OpenWebUI-Chat-Id": "chat-one"},
            json=request,
        )
        retry = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "X-OpenWebUI-Chat-Id": "chat-two"},
            json=request,
        )
        secret = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "assistant",
                "messages": [{"role": "user", "content": "credential candidate"}],
            },
        )
        story = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "storyteller",
                "messages": [{"role": "user", "content": "game-only fact"}],
            },
        )
        _wait_for_extractions(extracted_text, 3)
        records = client.get("/api/memory/v1/records", headers=AUTH).json()["data"]
        proposals = client.get("/api/memory/v1/proposals", headers=AUTH).json()["data"]
        status = client.get("/api/memory/v1/status", headers=AUTH).json()
        purged = client.request(
            "DELETE",
            f"/api/memory/v1/records/{records[0]['id']}",
            headers=AUTH,
            json={"reason": "verify automatic capture tombstone"},
        )
        after_purge_retry = client.post(
            "/v1/chat/completions",
            headers={**AUTH, "X-OpenWebUI-Chat-Id": "chat-three"},
            json=request,
        )
        _wait_for_extractions(extracted_text, 4)
        records_after_purge_retry = client.get("/api/memory/v1/records", headers=AUTH).json()[
            "data"
        ]

    assert all(response.status_code == 200 for response in (first, retry, secret, story))
    assert extracted_text == [
        "I prefer concise release notes.",
        "I prefer concise release notes.",
        "credential candidate",
        "I prefer concise release notes.",
    ]
    assert len(records) == 1
    assert records[0]["content"] == "The user prefers concise release notes."
    assert records[0]["source"] == "automatic-capture"
    assert proposals == []
    assert status["automatic"]["capture_mode"] == "automatic"
    assert purged.status_code == 200
    assert after_purge_retry.status_code == 200
    assert records_after_purge_retry == []
    repository = app.state.runtime.memory_service.repository
    assert [event["action"] for event in repository.audit_events] == [
        "record.create",
        "record.purge",
    ]
    assert all("content" not in event["metadata"] for event in repository.audit_events)


def test_stream_completion_captures_but_story_and_opt_out_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_SUBJECT_HMAC_KEY", "subject-test-secret")
    calls: list[str] = []

    class EventStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[]}\n\n'
            yield b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/health", "/models"}:
            return httpx.Response(200, json={"status": "ok", "data": []})
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(
                200,
                stream=EventStream(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404)

    async def extractor(user_text: str) -> list[dict]:
        calls.append(user_text)
        return [{"content": user_text, "importance": 0.5}]

    app = create_app(
        make_settings(memory_config_path=Path("tests/fixtures/memory-capture.yaml")),
        transport=httpx.MockTransport(handler),
        memory_store=FakeMemoryStore(),
        proposal_extractor=extractor,
    )
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "assistant",
                "stream": True,
                "messages": [{"role": "user", "content": "streamed durable fact"}],
            },
        ) as response:
            assert b"[DONE]" in b"".join(response.iter_bytes())
        _wait_for_proposals(client, AUTH, 1)
        client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "storyteller",
                "stream": True,
                "messages": [{"role": "user", "content": "game-only fact"}],
            },
        )
        client.patch(
            "/api/memory/v1/settings",
            headers=AUTH,
            json={"enabled": False, "capture_enabled": False, "retrieval_enabled": False},
        )
        client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "assistant",
                "stream": True,
                "messages": [{"role": "user", "content": "opted-out fact"}],
            },
        )
        time.sleep(0.05)

    assert calls == ["streamed durable fact"]


def test_fixed_service_principal_game_seam_is_idempotent() -> None:
    settings = make_settings(
        principal_api_keys_json=json.dumps(
            {"service-token": {"principal": "game-service", "kind": "service"}}
        )
    )
    app = create_app(settings, transport=_model_transport(), memory_store=FakeMemoryStore())
    service_auth = {"Authorization": "Bearer service-token"}
    with TestClient(app) as client:
        denied = client.post(
            "/api/memory/v1/internal/spaces",
            headers=AUTH,
            json={
                "display_name": "Campaign",
                "world_id": "world-1",
                "campaign_id": "campaign-1",
            },
        )
        created = client.post(
            "/api/memory/v1/internal/spaces",
            headers=service_auth,
            json={
                "display_name": "Campaign",
                "world_id": "world-1",
                "campaign_id": "campaign-1",
                "player_id": "player-1",
            },
        )
        space_id = created.json()["id"]
        first = client.post(
            f"/api/memory/v1/internal/spaces/{space_id}/events",
            headers=service_auth,
            json={"external_id": "event-1", "content": "The gate opened."},
        )
        second = client.post(
            f"/api/memory/v1/internal/spaces/{space_id}/events",
            headers=service_auth,
            json={"external_id": "event-1", "content": "The gate opened."},
        )
        context = client.post(
            "/api/memory/v1/internal/context",
            headers=service_auth,
            json={"space_id": space_id, "query": "gate", "limit": 10},
        )

    assert denied.status_code == 403
    assert created.status_code == 201
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert [item["content"] for item in context.json()["data"]] == ["The gate opened."]


@pytest.mark.asyncio
async def test_continuity_http_provider_contract_and_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTINUITY_API_TOKEN", "continuity-secret")
    observed: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer continuity-secret"
        if request.url.path == "/v1/info":
            return httpx.Response(
                200,
                json={
                    "contract": "continuity-http/1",
                    "capabilities": {
                        "health": {},
                        "context_load": {},
                        "ingest": {},
                        "list": {},
                        "correct": {},
                        "forget": {},
                        "hard_purge": {},
                        "export": {},
                    },
                },
            )
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        body = json.loads(request.content)
        observed.append((request.url.path, body))
        if request.url.path == "/v1/context/load":
            return httpx.Response(
                200,
                json={"record_hits": [{"record_id": "r1", "content": "Remember me."}]},
            )
        if request.url.path == "/v1/records/list":
            return httpx.Response(200, json={"items": [{"record_id": "r1", "content": "x"}]})
        if request.url.path == "/v1/records/correct":
            return httpx.Response(200, json={"record": {"record_id": "r1", "content": "fixed"}})
        if request.url.path == "/v1/records/export":
            return httpx.Response(200, json={"records": [{"record_id": "r1", "content": "x"}]})
        if request.url.path == "/v1/records/forget":
            return httpx.Response(200, json={"status": "ok", "forgotten": True})
        if request.url.path == "/v1/records/purge":
            return httpx.Response(200, json={"status": "ok", "purged": True})
        if request.url.path == "/v1/records":
            return httpx.Response(200, json={"status": "ok", "record_id": body["record_id"]})
        return httpx.Response(404)

    config = MemoryConfig.model_validate(
        {
            "version": 1,
            "provider": {
                "kind": "continuity-http",
                "base_url": "http://continuity.test",
                "token_env": "CONTINUITY_API_TOKEN",
            },
        }
    ).provider
    provider = ContinuityHttpProvider(config, transport=httpx.MockTransport(handler))
    scope = MemoryScope("personal", "space-1", "assistant", "subject-1")
    await provider.start()
    assert await provider.ping()
    assert (await provider.context_load(scope, query="remember", limit=3))[
        0
    ].content == "Remember me."
    created = await provider.ingest(
        scope, content="x", source="test", metadata={}, importance=0.5, record_id="r1"
    )
    assert created.id == "r1"
    assert (await provider.list_records(scope))[0].id == "r1"
    assert (await provider.correct(scope, "r1", content="fixed", reason="why")).content == "fixed"
    assert await provider.forget(scope, "r1", reason="why")
    assert await provider.purge(scope, "r1", reason="why")
    assert (await provider.export(scope))[0]["record_id"] == "r1"
    await provider.close()

    assert all(body["scope"]["namespace"] == "personal" for _, body in observed)
    assert "purge" in provider.capabilities
    context_body = next(body for path, body in observed if path == "/v1/context/load")
    assert context_body["record_limit"] == 3


def test_optional_continuity_outage_degrades_without_builtin_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTINUITY_API_TOKEN", "continuity-secret")

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    captured: list[dict] = []
    app = create_app(
        make_settings(memory_config_path=Path("tests/fixtures/memory-continuity.yaml")),
        transport=_model_transport(captured),
        memory_transport=httpx.MockTransport(offline),
    )
    with TestClient(app) as client:
        ready = client.get("/health/ready")
        chat = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={
                "model": "assistant",
                "messages": [{"role": "user", "content": "Continue without memory."}],
            },
        )
        status = client.get("/api/memory/v1/status", headers=AUTH)

    assert ready.status_code == 200
    assert ready.json()["status"] == "degraded"
    assert chat.status_code == 200
    assert status.json()["provider"]["kind"] == "continuity-http"
    assert status.json()["provider"]["healthy"] is False
    assert "untrusted reference data" not in json.dumps(captured[0]["messages"])


@pytest.mark.asyncio
async def test_bridge_requires_operator_policy_and_both_consents() -> None:
    config = MemoryConfig.model_validate(
        {
            "version": 1,
            "provider": {"kind": "builtin-postgres", "namespace": "personal"},
            "automatic": {
                "enabled": True,
                "capture": False,
                "retrieval": True,
                "capabilities": ["chat"],
            },
            "bridges": {
                "enabled": True,
                "operator_allowlist": [{"source_kind": "game", "target_kind": "personal"}],
            },
        }
    )
    store = FakeMemoryStore()
    repository = InMemoryMemoryRepository()
    service = MemoryService(
        config,
        BuiltinPostgresProvider(store),
        repository,
        subject_hmac_fallback="subject-secret",
    )
    await store.start()
    await service.start()
    principal = "owner"
    personal = await service.personal_space(principal)
    game = await repository.create_game_space(
        principal,
        "Campaign",
        "game",
        {"world_id": "world", "campaign_id": "campaign"},
    )
    await service.add_approved(
        principal,
        content="The moon gate is open.",
        source="game",
        metadata={},
        importance=0.7,
        space=game,
    )
    await service.set_bridge(
        principal,
        game.id,
        personal.id,
        source_consented=True,
        target_consented=False,
    )
    before = await service.context(
        principal, query="gate", limit=5, experience="chat", capability="chat"
    )
    await service.set_bridge(
        principal,
        game.id,
        personal.id,
        source_consented=True,
        target_consented=True,
    )
    after = await service.context(
        principal, query="gate", limit=5, experience="chat", capability="chat"
    )
    await service.close()
    await store.close()

    assert before == []
    assert [item.content for item in after] == ["The moon gate is open."]
    assert after[0].metadata["bridge_source_space_id"] == str(game.id)
