from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from local_ai_hub.config import load_profiles
from local_ai_hub.memory import MemoryRecord
from local_ai_hub.profiles import ProfileRegistry
from local_ai_hub.transform import InvalidChatRequest, prepare_chat_payload

LEGACY_PROFILES = Path("tests/fixtures/legacy-profiles.yaml")


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
