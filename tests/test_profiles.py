from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from local_ai_hub.config import RegistryDocument
from local_ai_hub.profiles import (
    ProfileRegistry,
    UnavailableProfileError,
    UnknownModelError,
)


def registry(data: dict[str, Any]) -> ProfileRegistry:
    return ProfileRegistry(RegistryDocument.model_validate(data))


def test_profile_can_select_highest_priority_compatible_model(
    registry_data: dict[str, Any],
) -> None:
    resolved = registry(registry_data).resolve(
        "chat", [{"role": "user", "content": "Hello"}], endpoint="chat"
    )
    assert resolved.profile_id == "chat"
    assert resolved.model_id == "story-model"
    assert resolved.provider_id == "mock"


def test_preferred_capability_breaks_equal_priority_tie(
    registry_data: dict[str, Any],
) -> None:
    data = deepcopy(registry_data)
    data["models"]["general-model"]["priority"] = 0
    data["models"]["story-model"]["priority"] = 0
    code = registry(data).resolve(
        "code", [{"role": "user", "content": "Implement this API"}], endpoint="chat"
    )
    story = registry(data).resolve(
        "story", [{"role": "user", "content": "Continue the scene"}], endpoint="chat"
    )
    assert code.model_id == "general-model"
    assert story.model_id == "story-model"


def test_automatic_routing_uses_registry_rules(registry_data: dict[str, Any]) -> None:
    resolved = registry(registry_data).resolve(
        "auto",
        [{"role": "user", "content": "/story Continue the scene"}],
        endpoint="chat",
    )
    assert resolved.profile_id == "story"
    assert "prefix" in resolved.route_reason


def test_direct_model_access_respects_endpoint(registry_data: dict[str, Any]) -> None:
    resolved = registry(registry_data).resolve(
        "image-model", [], endpoint="image"
    )
    assert resolved.upstream_model == "diffusion-upstream"
    with pytest.raises(UnavailableProfileError):
        registry(registry_data).resolve("image-model", [], endpoint="chat")


def test_unavailable_profile_reports_missing_capability(
    registry_data: dict[str, Any],
) -> None:
    data = deepcopy(registry_data)
    data["models"].pop("image-model")
    with pytest.raises(UnavailableProfileError, match="capabilities"):
        registry(data).resolve("image", [], endpoint="image")
    advertised = {item["id"] for item in registry(data).advertised_models()}
    assert "image" not in advertised
    assert "chat" in advertised


def test_unknown_model_is_distinct_from_unavailable(registry_data: dict[str, Any]) -> None:
    with pytest.raises(UnknownModelError):
        registry(registry_data).resolve("does-not-exist", [], endpoint="chat")


def test_capability_report_includes_unbound_profiles(registry_data: dict[str, Any]) -> None:
    data = deepcopy(registry_data)
    data["models"].pop("vision-model")
    report = registry(data).capability_report()
    assert report["profiles"]["vision"]["available"] is False
    assert report["profiles"]["image"]["available"] is True
