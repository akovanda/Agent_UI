from pathlib import Path

import pytest
from pydantic import ValidationError

from local_ai_hub.config import Settings, load_profiles
from local_ai_hub.profiles import (
    ProfileRegistry,
    UnavailableExperienceError,
    UnknownModelError,
)


def test_distributable_catalog_has_generic_experiences_and_no_models() -> None:
    document = load_profiles(Path("config/models/catalog.yaml"))
    assert document.version == 2
    assert document.models == {}
    assert {"chat", "code", "story", "image", "agent"}.issubset(document.profiles)
    registry = ProfileRegistry(document)
    advertised = {item["id"]: item for item in registry.advertised_models()}
    assert advertised["chat"]["metadata"]["available"] is False
    with pytest.raises(UnavailableExperienceError, match="register or enable"):
        registry.resolve("chat", [{"role": "user", "content": "hello"}])


def test_capability_selection_uses_priority_and_direct_model_access(tmp_path: Path) -> None:
    path = tmp_path / "catalog.yaml"
    path.write_text(
        """
version: 2
backends:
  local:
    kind: llama.cpp
    base_url: http://llama:8080
    coordinator: explicit
    serialize_requests: true
models:
  small:
    backend: local
    upstream_model: small-upstream
    priority: 10
    capabilities: [chat, code]
    artifact: {kind: container_path, path: /models/small.gguf}
  preferred:
    backend: local
    priority: 100
    capabilities:
      chat: {}
      story: {}
    features:
      reasoning:
        request_field: effort
        values: {fast: low, deep: high}
    artifact: {kind: container_path, path: /models/preferred.gguf}
experiences:
  chat:
    capability: chat
  code:
    capability: code
  story:
    capability: story
""".strip()
        + "\n",
        encoding="utf-8",
    )
    registry = ProfileRegistry(load_profiles(path))

    resolved = registry.resolve("chat", [{"role": "user", "content": "hello"}])
    assert resolved.backend_model == "preferred"
    assert resolved.backend_id == "local"
    assert resolved.model.features.reasoning.values["deep"] == "high"

    code = registry.resolve("code", [{"role": "user", "content": "fix this"}])
    assert code.backend_model == "small"
    assert code.model.upstream_model == "small-upstream"

    direct = registry.resolve("small", [], required_capability="chat")
    assert direct.profile_id == "small"


def test_pinned_experience_fails_closed_when_capability_is_not_declared(tmp_path: Path) -> None:
    path = tmp_path / "catalog.yaml"
    path.write_text(
        """
version: 2
backends:
  remote: {kind: openai-compatible, base_url: http://example/v1}
models:
  text:
    backend: remote
    capabilities: [chat]
experiences:
  image:
    model: text
    capability: image
""".strip()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_profiles(path)


def test_unknown_model_fails_closed() -> None:
    registry = ProfileRegistry(load_profiles(Path("config/models/catalog.yaml")))
    with pytest.raises(UnknownModelError):
        registry.resolve("invented", [{"role": "user", "content": "hello"}])


def test_explicit_model_coordination_rejects_multiple_gpu_requests() -> None:
    with pytest.raises(ValidationError, match="GPU_MAX_CONCURRENT_REQUESTS=1"):
        Settings(model_coordinator_mode="explicit", gpu_max_concurrent_requests=2)
