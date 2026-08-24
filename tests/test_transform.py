from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from local_ai_hub.config import load_profiles
from local_ai_hub.memory import MemoryRecord
from local_ai_hub.profiles import ProfileRegistry
from local_ai_hub.transform import (
    InvalidChatRequest,
    prepare_chat_payload,
    prepare_passthrough_payload,
)

LEGACY_PROFILES = Path("tests/fixtures/legacy-profiles.yaml")


def _alias_registry(tmp_path: Path) -> ProfileRegistry:
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        """
version: 2
backends:
  endpoint: {kind: openai-compatible, base_url: http://backend/v1}
models:
  quick:
    backend: endpoint
    upstream_model: shared-upstream
    capabilities: [chat, completions]
    defaults: {reasoning_effort: fast, temperature: 0.2, top_p: 0.8}
    features: &reasoning
      reasoning:
        request_field: effort
        values: {fast: low, balanced: medium, deep: high}
        unsupported_policy: reject
  thorough:
    backend: endpoint
    upstream_model: shared-upstream
    capabilities: [chat, completions]
    defaults: {reasoning_effort: deep, temperature: 0.4, top_p: 0.9}
    features: *reasoning
experiences:
  balanced:
    model: quick
    reasoning_effort: balanced
    defaults: {temperature: 0.6, max_tokens: 500}
""".lstrip(),
        encoding="utf-8",
    )
    return ProfileRegistry(load_profiles(catalog))


@pytest.mark.parametrize(
    ("model_id", "effort", "temperature"),
    [("quick", "low", 0.2), ("thorough", "high", 0.4)],
)
def test_direct_aliases_apply_their_own_model_defaults(
    tmp_path: Path,
    model_id: str,
    effort: str,
    temperature: float,
) -> None:
    registry = _alias_registry(tmp_path)
    resolved = registry.resolve(model_id, [], required_capability="chat")

    payload = prepare_chat_payload(
        {"model": model_id, "messages": [{"role": "user", "content": "Hello"}]},
        resolved,
        [],
        6000,
    )

    assert payload["model"] == "shared-upstream"
    assert payload["effort"] == effort
    assert payload["temperature"] == temperature
    assert payload["top_p"] == resolved.model.defaults["top_p"]
    metadata = next(item for item in registry.advertised_models() if item["id"] == model_id)[
        "metadata"
    ]
    assert metadata["reasoning_default"] == resolved.model.defaults["reasoning_effort"]
    assert "defaults" not in metadata


def test_experience_and_request_values_override_model_defaults(tmp_path: Path) -> None:
    registry = _alias_registry(tmp_path)
    resolved = registry.resolve("balanced", [])

    experience_payload = prepare_chat_payload(
        {"model": "balanced", "messages": [{"role": "user", "content": "Hello"}]},
        resolved,
        [],
        6000,
    )
    request_payload = prepare_chat_payload(
        {
            "model": "balanced",
            "messages": [{"role": "user", "content": "Hello"}],
            "reasoning_effort": "deep",
            "temperature": 0.9,
            "max_tokens": 50,
        },
        resolved,
        [],
        6000,
    )

    assert experience_payload["effort"] == "medium"
    assert experience_payload["temperature"] == 0.6
    assert experience_payload["max_tokens"] == 500
    assert experience_payload["top_p"] == 0.8
    assert request_payload["effort"] == "high"
    assert request_payload["temperature"] == 0.9
    assert request_payload["max_tokens"] == 50


def test_header_reasoning_override_wins_over_request_and_defaults(tmp_path: Path) -> None:
    registry = _alias_registry(tmp_path)
    resolved = registry.resolve("balanced", [])

    payload = prepare_chat_payload(
        {
            "model": "balanced",
            "messages": [{"role": "user", "content": "Hello"}],
            "reasoning_effort": "deep",
        },
        resolved,
        [],
        6000,
        reasoning_override="fast",
    )

    assert payload["effort"] == "low"


def test_model_defaults_apply_to_passthrough_and_request_values_win(tmp_path: Path) -> None:
    registry = _alias_registry(tmp_path)
    resolved = registry.resolve("quick", [], required_capability="completions")

    payload = prepare_passthrough_payload(
        {"model": "quick", "prompt": "Once", "temperature": 0.75},
        resolved,
    )

    assert payload == {
        "model": "shared-upstream",
        "prompt": "Once",
        "temperature": 0.75,
        "top_p": 0.8,
        "effort": "low",
    }


def test_general_payload_uses_native_reasoning_and_one_developer_message() -> None:
    registry = ProfileRegistry(load_profiles(LEGACY_PROFILES))
    resolved = registry.resolve(
        "assistant", [{"role": "user", "content": "Check the barn network."}]
    )
    now = datetime.now(UTC)
    memory = MemoryRecord(
        id=uuid4(),
        user_id="andrew",
        namespace="infrastructure",
        content="The barn server is a UCS C240 M5 with a Tesla T4.",
        source="manual",
        metadata={},
        importance=0.8,
        created_at=now,
        updated_at=now,
    )
    payload = prepare_chat_payload(
        {"model": "assistant", "messages": [{"role": "user", "content": "Hi"}]},
        resolved,
        [memory],
        6000,
    )
    assert payload["model"] == "gpt-oss-20b"
    assert payload["reasoning_effort"] == "medium"
    assert payload["messages"][0]["role"] == "developer"
    assert "Experience instructions" in payload["messages"][0]["content"]
    assert "untrusted reference data" in payload["messages"][0]["content"]
    assert "Tesla T4" in payload["messages"][0]["content"]
    assert sum(message["role"] == "developer" for message in payload["messages"]) == 1
    assert payload["temperature"] == 0.55


def test_client_instruction_messages_are_consolidated_for_gpt_oss() -> None:
    registry = ProfileRegistry(load_profiles(LEGACY_PROFILES))
    messages = [
        {"role": "system", "content": "Use metric units."},
        {
            "role": "developer",
            "content": [{"type": "text", "text": "Return executable commands."}],
        },
        {"role": "user", "content": "Diagnose the host."},
    ]
    resolved = registry.resolve("gpt-oss-20b", messages)
    payload = prepare_chat_payload(
        {"model": "gpt-oss-20b", "messages": messages, "reasoning_effort": "high"},
        resolved,
        [],
        6000,
    )
    assert payload["reasoning_effort"] == "high"
    assert payload["messages"][0]["role"] == "developer"
    assert "Use metric units." in payload["messages"][0]["content"]
    assert "Return executable commands." in payload["messages"][0]["content"]
    assert payload["messages"][1] == {"role": "user", "content": "Diagnose the host."}


def test_story_payload_uses_creative_sampler_without_reasoning_field() -> None:
    registry = ProfileRegistry(load_profiles(LEGACY_PROFILES))
    resolved = registry.resolve(
        "storyteller", [{"role": "user", "content": "/story Open on the hangar deck."}]
    )
    payload = prepare_chat_payload(
        {
            "model": "storyteller",
            "messages": [
                {"role": "system", "content": "Use close third person."},
                {"role": "user", "content": "/story Begin."},
            ],
            "reasoning_effort": "high",
        },
        resolved,
        [],
        6000,
    )
    assert payload["model"] == "stheno-8b"
    assert "reasoning_effort" not in payload
    assert payload["messages"][0]["role"] == "system"
    assert "Use close third person." in payload["messages"][0]["content"]
    assert payload["messages"][-1]["content"] == "Begin."
    assert payload["temperature"] == 1.3


def test_instruction_message_without_text_is_rejected() -> None:
    registry = ProfileRegistry(load_profiles(LEGACY_PROFILES))
    messages = [
        {"role": "system", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
        {"role": "user", "content": "Hi"},
    ]
    resolved = registry.resolve("gpt-oss-20b", messages)
    with pytest.raises(InvalidChatRequest, match="must contain text"):
        prepare_chat_payload(
            {"model": "gpt-oss-20b", "messages": messages},
            resolved,
            [],
            6000,
        )
