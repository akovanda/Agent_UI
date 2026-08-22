from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from local_ai_hub.config import RegistryDocument
from local_ai_hub.profiles import ProfileRegistry
from local_ai_hub.transform import InvalidChatRequest, prepare_chat_payload


def resolve(data: dict[str, Any], profile: str):
    return ProfileRegistry(RegistryDocument.model_validate(data)).resolve(
        profile,
        [{"role": "user", "content": "test"}],
        endpoint="chat",
    )


def test_flat_reasoning_and_developer_instructions(registry_data: dict[str, Any]) -> None:
    data = deepcopy(registry_data)
    data["models"]["story-model"]["priority"] = 0
    resolved = resolve(data, "code")
    prepared = prepare_chat_payload(
        {
            "model": "code",
            "messages": [
                {"role": "system", "content": "Keep output concise."},
                {"role": "user", "content": "/code Implement it"},
            ],
        },
        resolved,
        [],
        1000,
        route_prefixes=["/code"],
    )
    assert prepared.payload["model"] == "general-model"
    assert prepared.payload["reasoning_effort"] == "high"
    assert prepared.applied_effort == "high"
    assert prepared.payload["messages"][0]["role"] == "developer"
    assert "Write tested code" in prepared.payload["messages"][0]["content"]
    assert "Keep output concise" in prepared.payload["messages"][0]["content"]
    assert prepared.payload["messages"][1]["content"] == "Implement it"


def test_explicit_effort_rejected_when_model_does_not_support_it(
    registry_data: dict[str, Any],
) -> None:
    resolved = resolve(registry_data, "story")
    with pytest.raises(InvalidChatRequest, match="does not advertise"):
        prepare_chat_payload(
            {"model": "story", "messages": [{"role": "user", "content": "Hi"}]},
            resolved,
            [],
            1000,
            reasoning_override="high",
        )


def test_profile_reasoning_preference_is_ignored_when_unsupported(
    registry_data: dict[str, Any],
) -> None:
    data = deepcopy(registry_data)
    data["profiles"]["story"]["reasoning_effort"] = "high"
    resolved = resolve(data, "story")
    prepared = prepare_chat_payload(
        {"model": "story", "messages": [{"role": "user", "content": "Hi"}]},
        resolved,
        [],
        1000,
    )
    assert prepared.requested_effort == "high"
    assert prepared.applied_effort is None
    assert "reasoning_effort" not in prepared.payload


def test_object_reasoning_transport(registry_data: dict[str, Any]) -> None:
    data = deepcopy(registry_data)
    data["models"]["story-model"]["priority"] = 0
    reasoning = data["models"]["general-model"]["features"]["reasoning"]
    reasoning.update({"transport": "object", "field": "reasoning"})
    resolved = resolve(data, "chat-deep")
    prepared = prepare_chat_payload(
        {"model": "chat-deep", "messages": [{"role": "user", "content": "Think"}]},
        resolved,
        [],
        1000,
    )
    assert prepared.payload["reasoning"] == {"effort": "high"}


def test_chat_template_reasoning_transport(registry_data: dict[str, Any]) -> None:
    data = deepcopy(registry_data)
    data["models"]["story-model"]["priority"] = 0
    reasoning = data["models"]["general-model"]["features"]["reasoning"]
    reasoning.update(
        {
            "transport": "chat_template",
            "field": "reasoning_effort",
            "aliases": {"xhigh": "high"},
        }
    )
    resolved = resolve(data, "chat")
    prepared = prepare_chat_payload(
        {
            "model": "chat",
            "reasoning_effort": "xhigh",
            "messages": [{"role": "user", "content": "Think"}],
        },
        resolved,
        [],
        1000,
    )
    assert prepared.payload["chat_template_kwargs"] == {"reasoning_effort": "high"}


def test_multimodal_user_content_is_preserved(registry_data: dict[str, Any]) -> None:
    resolved = resolve(registry_data, "vision")
    content = [
        {"type": "text", "text": "Describe this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]
    prepared = prepare_chat_payload(
        {"model": "vision", "messages": [{"role": "user", "content": content}]},
        resolved,
        [],
        1000,
    )
    assert prepared.payload["messages"][-1]["content"] == content
