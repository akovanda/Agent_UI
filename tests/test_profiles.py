from pathlib import Path

import pytest

from local_ai_hub.config import load_profiles
from local_ai_hub.profiles import ProfileRegistry, UnknownModelError


def registry() -> ProfileRegistry:
    return ProfileRegistry(load_profiles(Path("config/gateway/profiles.yaml")))


def test_advertised_models_are_virtual_interfaces() -> None:
    ids = {item["id"] for item in registry().advertised_models()}
    assert {"auto", "assistant", "storyteller"}.issubset(ids)


def test_auto_profile_resolves_to_story_backend() -> None:
    resolved = registry().resolve(
        "auto", [{"role": "user", "content": "Continue our campaign scene."}]
    )
    assert resolved.profile_id == "storyteller"
    assert resolved.backend_model == "stheno-8b"


def test_unknown_model_fails_closed() -> None:
    with pytest.raises(UnknownModelError):
        registry().resolve("invented-model", [{"role": "user", "content": "hello"}])


def test_explicit_model_coordination_rejects_multiple_gpu_requests() -> None:
    from pydantic import ValidationError

    from local_ai_hub.config import Settings

    with pytest.raises(ValidationError, match="GPU_MAX_CONCURRENT_REQUESTS=1"):
        Settings(model_coordinator_mode="explicit", gpu_max_concurrent_requests=2)


def test_compact_generated_profile_catalog_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text(
        """
{
  "auto": {
    "route": "auto",
    "description": "Choose automatically"
  },
  "assistant": {
    "model": "gpt-oss-20b",
    "temperature": 0.55,
    "reasoning_effort": "medium",
    "memory": {"enabled": true, "namespaces": ["general"]}
  },
  "storyteller": {
    "model": "stheno-8b",
    "temperature": 1.3,
    "reasoning_effort": "none"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    document = load_profiles(path)

    assert set(document.backends) == {"gpt-oss-20b", "stheno-8b"}
    assert document.profiles["auto"].route == "auto"
    assert document.profiles["assistant"].backend_model == "gpt-oss-20b"
    assert document.profiles["assistant"].reasoning_effort == "medium"
    assert document.profiles["assistant"].defaults["temperature"] == 0.55
    assert document.profiles["assistant"].memory.namespaces == ["general"]
    assert document.profiles["storyteller"].reasoning_effort is None
