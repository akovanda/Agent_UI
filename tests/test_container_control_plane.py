from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from ops import registryctl


def write_registry(path: Path, value: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def common(base: Path, overlay: Path) -> dict[str, str]:
    return {"registry": str(base), "overlay": str(overlay)}


def test_empty_generic_registry_is_valid() -> None:
    registry = registryctl.load_registry(Path("config/registry.yaml"), None)
    assert registry["version"] == 2
    assert registry["models"] == {}
    assert {"chat", "code", "story", "image", "embedding"}.issubset(
        registry["profiles"]
    )


def test_host_sources_render_read_only_compose_mounts(
    tmp_path: Path, registry_data: dict[str, Any]
) -> None:
    data = deepcopy(registry_data)
    data["sources"]["shared"] = {
        "type": "host",
        "mount_path": "/model-sources/shared",
        "host_path": str(tmp_path),
        "read_only": True,
    }
    output = yaml.safe_load(registryctl.render_compose_override(data))
    for service in ("config-init", "llama", "toolbox"):
        mount = output["services"][service]["volumes"][0]
        assert mount["source"] == str(tmp_path)
        assert mount["target"] == "/model-sources/shared"
        assert mount["read_only"] is True
        assert mount["bind"]["create_host_path"] is False


def test_llama_preset_uses_registered_source_path(
    tmp_path: Path, registry_data: dict[str, Any]
) -> None:
    data = deepcopy(registry_data)
    data["providers"]["mock"]["type"] = "llama_cpp"
    data["sources"]["shared"] = {
        "type": "host",
        "mount_path": "/model-sources/shared",
        "host_path": str(tmp_path),
        "read_only": True,
    }
    data["models"]["general-model"]["artifact"] = {
        "source": "shared",
        "path": "project/general.gguf",
    }
    data["models"]["general-model"]["runtime"] = {
        "ctx-size": 32768,
        "n-gpu-layers": "all",
    }
    preset = registryctl.render_models_ini(data, 1024)
    assert "[general-model]" in preset
    assert "model = /model-sources/shared/project/general.gguf" in preset
    assert "ctx-size = 32768" in preset


def test_source_and_model_can_be_registered_from_existing_host_path(
    tmp_path: Path, registry_data: dict[str, Any]
) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "overlay.yaml"
    write_registry(base, registry_data)
    model_file = tmp_path / "project" / "model.gguf"
    model_file.parent.mkdir()
    model_file.write_bytes(b"GGUF-test")

    args = argparse.Namespace(
        **common(base, overlay),
        model_id="my-local-model",
        provider="mock",
        capability=["chat,code", "reasoning"],
        tag=["general"],
        source=None,
        path=None,
        host_path=str(model_file),
        upstream_model=None,
        coordinator_model=None,
        display_name=None,
        description=None,
        priority=20,
        runtime=["ctx-size=16384"],
        reasoning_transport="flat",
        reasoning_field="reasoning_effort",
        reasoning_level=["low,medium,high"],
        developer_role=True,
        tool_calling=True,
        no_streaming=False,
        disabled=False,
        bind_profile="code",
    )
    assert registryctl.cmd_model_register(args) == 0
    merged = registryctl.load_registry(base, overlay)
    model = merged["models"]["my-local-model"]
    source = merged["sources"][model["artifact"]["source"]]
    assert source["host_path"] == str(model_file.parent)
    assert model["artifact"]["path"] == "model.gguf"
    assert model["capabilities"] == ["chat", "code", "reasoning"]
    assert merged["profiles"]["code"]["model"] == "my-local-model"


def test_registry_apply_is_declarative_and_validated(
    tmp_path: Path, registry_data: dict[str, Any]
) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "overlay.yaml"
    manifest = tmp_path / "manifest.yaml"
    write_registry(base, registry_data)
    write_registry(
        manifest,
        {
            "version": 2,
            "providers": {
                "remote": {
                    "type": "openai_compatible",
                    "base_url": "http://remote.test/v1",
                    "api_key_env": "REMOTE_KEY",
                }
            },
            "models": {
                "remote-chat": {
                    "provider": "remote",
                    "capabilities": ["chat", "reasoning"],
                    "features": {
                        "reasoning": {
                            "transport": "object",
                            "field": "reasoning",
                            "levels": ["low", "high"],
                        }
                    },
                }
            },
        },
    )
    args = argparse.Namespace(
        **common(base, overlay),
        input=str(manifest),
        replace=False,
        dry_run=False,
    )
    assert registryctl.cmd_registry_apply(args) == 0
    merged = registryctl.load_registry(base, overlay)
    assert merged["models"]["remote-chat"]["provider"] == "remote"


def test_kubernetes_requires_explicit_mapping_for_host_sources(
    tmp_path: Path, registry_data: dict[str, Any]
) -> None:
    data = deepcopy(registry_data)
    data["providers"]["mock"]["type"] = "llama_cpp"
    data["sources"]["shared"] = {
        "type": "host",
        "mount_path": "/model-sources/shared",
        "host_path": str(tmp_path),
        "read_only": True,
    }
    data["models"]["general-model"]["artifact"] = {
        "source": "shared",
        "path": "model.gguf",
    }
    base = tmp_path / "base.yaml"
    output = tmp_path / "values.yaml"
    write_registry(base, data)
    args = argparse.Namespace(
        registry=str(base),
        overlay=str(tmp_path / "missing.yaml"),
        output=str(output),
    )
    with pytest.raises(registryctl.HubError, match="kubernetes mapping"):
        registryctl.cmd_registry_k8s_values(args)


def test_kubernetes_values_support_existing_claim(
    tmp_path: Path, registry_data: dict[str, Any]
) -> None:
    data = deepcopy(registry_data)
    data["providers"]["mock"]["type"] = "llama_cpp"
    data["sources"]["shared"] = {
        "type": "host",
        "mount_path": "/model-sources/shared",
        "host_path": str(tmp_path),
        "read_only": True,
        "kubernetes": {"type": "existingClaim", "claimName": "shared-models"},
    }
    data["models"]["general-model"]["artifact"] = {
        "source": "shared",
        "path": "model.gguf",
    }
    base = tmp_path / "base.yaml"
    output = tmp_path / "values.yaml"
    write_registry(base, data)
    args = argparse.Namespace(
        registry=str(base),
        overlay=str(tmp_path / "missing.yaml"),
        output=str(output),
    )
    assert registryctl.cmd_registry_k8s_values(args) == 0
    values = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert values["modelSources"]["shared"]["claimName"] == "shared-models"
    assert values["models"]["general-model"]["containerPath"].endswith("model.gguf")


def test_plan_is_machine_readable(
    tmp_path: Path, registry_data: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "base.yaml"
    write_registry(base, registry_data)
    args = argparse.Namespace(
        registry=str(base),
        overlay=str(tmp_path / "missing.yaml"),
        json=True,
    )
    assert registryctl.cmd_registry_plan(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["profiles"]["image"]["available"] is True
    assert value["providers"]["mock"]["type"] == "openai_compatible"
