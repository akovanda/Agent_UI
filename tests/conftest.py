from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from local_ai_hub.config import Settings


@pytest.fixture
def registry_data() -> dict[str, Any]:
    return {
        "version": 2,
        "providers": {
            "mock": {
                "type": "openai_compatible",
                "base_url": "http://provider.test/v1",
                "health_path": None,
                "endpoints": {
                    "chat": "chat/completions",
                    "image": "images/generations",
                    "embedding": "embeddings",
                    "rerank": "rerank",
                },
            }
        },
        "sources": {
            "managed": {
                "type": "managed",
                "mount_path": "/models",
                "read_only": False,
            }
        },
        "models": {
            "general-model": {
                "provider": "mock",
                "priority": 10,
                "capabilities": ["chat", "code", "tools", "reasoning"],
                "tags": ["general"],
                "features": {
                    "developer_role": True,
                    "tool_calling": True,
                    "reasoning": {
                        "transport": "flat",
                        "field": "reasoning_effort",
                        "levels": ["low", "medium", "high"],
                    },
                },
            },
            "story-model": {
                "provider": "mock",
                "priority": 20,
                "capabilities": ["chat", "story", "long_context"],
                "tags": ["creative"],
            },
            "vision-model": {
                "provider": "mock",
                "capabilities": ["chat", "vision"],
            },
            "image-model": {
                "provider": "mock",
                "upstream_model": "diffusion-upstream",
                "capabilities": ["image"],
            },
            "embedding-model": {
                "provider": "mock",
                "capabilities": ["embedding"],
            },
            "rerank-model": {
                "provider": "mock",
                "capabilities": ["rerank"],
            },
        },
        "profiles": {
            "auto": {"route": "auto", "endpoint": "chat"},
            "chat": {
                "endpoint": "chat",
                "requires": {"all_of": ["chat"]},
                "defaults": {"temperature": 0.5},
            },
            "chat-deep": {
                "endpoint": "chat",
                "reasoning_effort": "high",
                "requires": {
                    "all_of": ["chat"],
                    "prefer_capabilities": ["reasoning"],
                },
            },
            "code": {
                "endpoint": "chat",
                "reasoning_effort": "high",
                "system_prompt": "Write tested code.",
                "requires": {
                    "all_of": ["chat"],
                    "prefer_capabilities": ["code"],
                },
            },
            "story": {
                "endpoint": "chat",
                "requires": {
                    "all_of": ["chat"],
                    "prefer_capabilities": ["story"],
                },
            },
            "vision": {
                "endpoint": "chat",
                "requires": {"all_of": ["chat", "vision"]},
            },
            "image": {
                "endpoint": "image",
                "requires": {"all_of": ["image"]},
            },
            "embedding": {
                "endpoint": "embedding",
                "requires": {"all_of": ["embedding"]},
            },
            "rerank": {
                "endpoint": "rerank",
                "requires": {"all_of": ["rerank"]},
            },
            "agent": {
                "endpoint": "chat",
                "advertised": False,
                "requires": {"all_of": ["chat", "tools"]},
            },
        },
        "routes": [
            {
                "profile": "story",
                "priority": 100,
                "prefixes": ["/story"],
                "patterns": [r"\b(?:story|scene|roleplay)\b"],
            },
            {
                "profile": "code",
                "priority": 90,
                "prefixes": ["/code"],
                "patterns": [r"\b(?:debug|implement|python|api)\b"],
            },
            {"profile": "chat", "priority": 0, "prefixes": ["/chat"]},
        ],
    }


@pytest.fixture
def registry_path(tmp_path: Path, registry_data: dict[str, Any]) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump(registry_data, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def settings(registry_path: Path) -> Settings:
    return Settings(
        registry_config_path=registry_path,
        gateway_api_key="test-key",
        memory_enabled=False,
        memory_required=False,
        cors_origins="http://localhost",
    )
