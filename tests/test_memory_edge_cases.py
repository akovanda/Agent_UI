from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import Request

from conftest import FakeMemoryStore, make_settings
from local_ai_hub.identity import (
    IdentityError,
    authenticate_request,
    decode_hs256_jwt,
    parse_principal_key_map,
    pseudonymous_subject,
)
from local_ai_hub.memory import NullMemoryStore
from local_ai_hub.memory_capture import (
    CaptureQueue,
    CaptureRequest,
    extraction_messages,
    parse_extraction_response,
)
from local_ai_hub.memory_config import MemoryConfig, load_memory_config
from local_ai_hub.memory_provider import (
    BuiltinPostgresProvider,
    DisabledMemoryProvider,
    MemoryProviderError,
    MemoryScope,
)
from local_ai_hub.memory_repository import InMemoryMemoryRepository
from local_ai_hub.memory_service import MemoryConflictError, MemoryService


def _token(header: dict, payload: object, secret: str) -> str:
    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    head = encode(header)
    body = encode(payload)
    signature = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _request(headers: dict[str, str] | None = None, cookies: str = "") -> Request:
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    if cookies:
        raw_headers.append((b"cookie", cookies.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/memory/v1/status",
            "headers": raw_headers,
            "client": ("127.0.0.1", 1234),
        }
    )


def test_jwt_validation_and_principal_key_configuration_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "identity-secret"
    valid = _token({"alg": "HS256"}, {"id": "user-1", "exp": time.time() + 30}, secret)
    assert decode_hs256_jwt(valid, secret)["id"] == "user-1"
    assert pseudonymous_subject("user-1", secret) == pseudonymous_subject("user-1", secret)

    with pytest.raises(IdentityError, match="not configured"):
        decode_hs256_jwt(valid, "")
    with pytest.raises(IdentityError, match=r"invalid JWT$"):
        decode_hs256_jwt("two.parts", secret)
    with pytest.raises(IdentityError, match="HS256"):
        decode_hs256_jwt(_token({"alg": "none"}, {"sub": "x"}, secret), secret)
    with pytest.raises(IdentityError, match="signature"):
        decode_hs256_jwt(valid, "wrong")
    with pytest.raises(IdentityError, match="expired"):
        decode_hs256_jwt(
            _token({"alg": "HS256"}, {"sub": "x", "exp": time.time() - 1}, secret),
            secret,
        )
    with pytest.raises(IdentityError, match="not active"):
        decode_hs256_jwt(
            _token({"alg": "HS256"}, {"sub": "x", "nbf": time.time() + 30}, secret),
            secret,
        )
    with pytest.raises(IdentityError, match="time claim"):
        decode_hs256_jwt(_token({"alg": "HS256"}, {"sub": "x", "exp": "later"}, secret), secret)

    assert parse_principal_key_map(make_settings()) == {}
    settings = make_settings(
        principal_api_keys_json=json.dumps(
            {"one": "user-1", "two": {"principal": "svc", "kind": "service"}}
        )
    )
    assert parse_principal_key_map(settings) == {
        "one": ("user-1", "user"),
        "two": ("svc", "service"),
    }
    with pytest.raises(IdentityError, match="valid JSON"):
        parse_principal_key_map(make_settings(principal_api_keys_json="{"))
    with pytest.raises(IdentityError, match="must be an object"):
        parse_principal_key_map(make_settings(principal_api_keys_json="[]"))
    with pytest.raises(IdentityError, match="bindings"):
        parse_principal_key_map(make_settings(principal_api_keys_json='{"key": 7}'))

    monkeypatch.setenv("WEBUI_SECRET_KEY", secret)
    forwarded = _request(
        {
            "Authorization": "Bearer test-secret-key",
            "X-OpenWebUI-User-Jwt": valid,
        }
    )
    principal = authenticate_request(
        forwarded,
        settings,
        load_memory_config(Path("config/memory/base.yaml")).identity,
        allow_browser_cookie=False,
    )
    assert principal.principal_id == "user-1"
    with pytest.raises(IdentityError, match="no subject"):
        authenticate_request(
            _request(
                {
                    "Authorization": "Bearer test-secret-key",
                    "X-OpenWebUI-User-Jwt": _token(
                        {"alg": "HS256"}, {"exp": time.time() + 30}, secret
                    ),
                }
            ),
            settings,
            load_memory_config(Path("config/memory/base.yaml")).identity,
            allow_browser_cookie=False,
        )


def test_memory_configuration_merge_and_validation(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "overlay.yaml"
    base.write_text(
        "version: 1\nprovider:\n  kind: builtin-postgres\nautomatic:\n  enabled: false\n",
        encoding="utf-8",
    )
    overlay.write_text(
        "automatic:\n  enabled: true\n  capture: false\n",
        encoding="utf-8",
    )
    merged = load_memory_config(base, overlay)
    assert merged.automatic.enabled is True
    assert merged.automatic.capture is False
    assert merged.provider.token == ""
    with pytest.raises(FileNotFoundError):
        load_memory_config(tmp_path / "missing.yaml")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_memory_config(invalid)
    with pytest.raises(ValueError, match="base_url"):
        MemoryConfig.model_validate({"version": 1, "provider": {"kind": "continuity-http"}})


def test_extraction_response_parser_accepts_only_structured_candidates() -> None:
    candidate = {"content": "durable", "kind": "fact", "importance": 0.5}
    assert parse_extraction_response(json.dumps({"candidates": [candidate]})) == [candidate]
    assert parse_extraction_response(f"```json\n{json.dumps([candidate])}\n```") == [candidate]
    assert parse_extraction_response(f"prefix {json.dumps([candidate])} suffix") == [candidate]
    assert parse_extraction_response("not json") == []
    assert parse_extraction_response('{"candidates":"bad"}') == []
    messages = extraction_messages("hello")
    assert messages[-1] == {"role": "user", "content": "hello"}
    assert "credentials" in messages[0]["content"]


@pytest.mark.asyncio
async def test_capture_queue_full_and_extractor_failure_are_nonfatal() -> None:
    config = MemoryConfig.model_validate(
        {
            "version": 1,
            "provider": {"kind": "builtin-postgres"},
            "automatic": {
                "enabled": True,
                "capture": True,
                "retrieval": False,
                "capabilities": ["chat"],
                "queue_size": 1,
            },
        }
    )
    store = FakeMemoryStore()
    service = MemoryService(
        config,
        BuiltinPostgresProvider(store),
        InMemoryMemoryRepository(),
        subject_hmac_fallback="subject-secret",
    )
    await store.start()
    await service.start()

    async def failing(_text: str) -> list[dict]:
        raise RuntimeError("extractor failed")

    queue = CaptureQueue(service, failing)
    request = CaptureRequest("user", "chat", "chat", "durable", None)
    assert await queue.enqueue(request)
    assert not await queue.enqueue(request)
    queued = await queue.queue.get()
    queue.queue.task_done()
    assert queued == request

    await queue.start()
    assert await queue.enqueue(request)
    await asyncio.wait_for(queue.queue.join(), timeout=1)
    await queue.close()
    await service.close()
    await store.close()


@pytest.mark.asyncio
async def test_disabled_provider_and_null_store_lifecycle_are_explicit() -> None:
    provider = DisabledMemoryProvider()
    scope = MemoryScope("personal", "space", "assistant", "subject")
    await provider.start()
    assert await provider.ping()
    assert await provider.context_load(scope, query="x", limit=1) == []
    assert await provider.list_records(scope) == []
    assert await provider.export(scope) == []
    assert not await provider.forget(scope, "record", reason="x")
    assert not await provider.purge(scope, "record", reason="x")
    with pytest.raises(MemoryProviderError, match="disabled"):
        await provider.ingest(scope, content="x", source=None, metadata={}, importance=0.5)
    with pytest.raises(MemoryProviderError, match="disabled"):
        await provider.correct(scope, "record", content="x", reason="x")
    await provider.close()

    store = NullMemoryStore()
    assert await store.list_records(user_id="x", namespace="x") == []
    assert not await store.forget(user_id="x", namespace="x", record_id="x")
    assert not await store.purge(user_id="x", namespace="x", record_id="x")
    assert (
        await store.migrate_legacy(
            user_id="x", target_user_id="y", target_namespace="z", namespaces=[]
        )
        == 0
    )
    with pytest.raises(RuntimeError, match="disabled"):
        await store.correct(user_id="x", namespace="x", record_id="x", content="x")


@pytest.mark.asyncio
async def test_expired_proposal_cannot_be_approved() -> None:
    config = MemoryConfig.model_validate({"version": 1, "provider": {"kind": "builtin-postgres"}})
    store = FakeMemoryStore()
    repository = InMemoryMemoryRepository()
    service = MemoryService(
        config,
        BuiltinPostgresProvider(store),
        repository,
        subject_hmac_fallback="subject-secret",
    )
    await service.start()
    proposals = await service.add_proposals(
        "user",
        candidates=[{"content": "The user prefers concise answers."}],
        experience="chat",
        chat_hash=None,
    )
    proposal = proposals[0]
    repository.proposals[proposal.id] = replace(
        proposal, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    with pytest.raises(MemoryConflictError, match="expired"):
        await service.approve_proposal("user", proposal.id)

    expired = repository.proposals[proposal.id]
    assert expired.state == "expired"
    assert expired.content is None
    assert repository.references == {}
    assert repository.audit_events[-1]["action"] == "proposal.expire"
    await service.close()


@pytest.mark.asyncio
async def test_builtin_provider_honors_scoped_idempotency_keys() -> None:
    store = FakeMemoryStore()
    provider = BuiltinPostgresProvider(store)
    personal = MemoryScope("personal", "space-1", "assistant", "subject-1")
    other_space = MemoryScope("personal", "space-2", "assistant", "subject-1")

    first = await provider.ingest(
        personal,
        content="First write wins.",
        source="proposal",
        metadata={},
        importance=0.5,
        record_id="proposal-1",
    )
    retry = await provider.ingest(
        personal,
        content="A concurrent retry.",
        source="proposal",
        metadata={},
        importance=0.5,
        record_id="proposal-1",
    )
    separate = await provider.ingest(
        other_space,
        content="The same external key in another space.",
        source="proposal",
        metadata={},
        importance=0.5,
        record_id="proposal-1",
    )

    assert first.id == retry.id
    assert retry.content == "First write wins."
    assert separate.id != first.id
    assert len(store.records) == 2
