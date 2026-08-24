from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location("hubctl", Path("ops/hubctl.py"))
assert spec and spec.loader
hubctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hubctl)


def base_catalog() -> dict:
    return {
        "version": 2,
        "backends": {
            "local": {
                "kind": "llama.cpp",
                "base_url": "http://llama:8080",
            },
            "remote": {
                "kind": "openai-compatible",
                "base_url": "http://remote/v1",
            },
        },
        "models": {},
        "experiences": {"chat": {"capability": "chat"}},
    }


def test_empty_catalog_is_valid() -> None:
    catalog = base_catalog()
    hubctl.validate_catalog(catalog)
    assert hubctl.render_models_ini(catalog, Path("/models"), 1024).startswith("version = 1")


def test_host_path_and_external_volume_render_to_compose_override() -> None:
    catalog = base_catalog()
    catalog["models"] = {
        "host-model": {
            "backend": "local",
            "capabilities": ["chat"],
            "artifact": {"kind": "host_path", "path": "/srv/models/one.gguf"},
        },
        "volume-model": {
            "backend": "local",
            "capabilities": {"code": {}},
            "artifact": {
                "kind": "docker_volume",
                "volume": "shared-cache",
                "sub_path": "two.gguf",
            },
        },
        "endpoint-model": {
            "backend": "remote",
            "capabilities": ["chat"],
            "artifact": {"kind": "none"},
        },
    }
    hubctl.validate_catalog(catalog)
    override = hubctl.render_compose_override(catalog)
    mounts = override["services"]["llama"]["volumes"]
    assert mounts[0]["type"] == "bind"
    assert mounts[0]["source"] == "/srv/models/one.gguf"
    assert mounts[0]["target"] == "/models/external/host-model/one.gguf"
    assert mounts[0]["read_only"] is True
    assert mounts[1]["type"] == "volume"
    assert override["volumes"]["external-volume-model"]["external"] is True
    assert override["volumes"]["external-volume-model"]["name"] == "shared-cache"


def test_runtime_preset_uses_stable_upstream_id_and_external_path() -> None:
    catalog = base_catalog()
    catalog["models"] = {
        "registered": {
            "backend": "local",
            "upstream_model": "runtime-name",
            "capabilities": ["chat"],
            "artifact": {"kind": "host_path", "path": "/project/model.gguf"},
            "runtime": {"ctx-size": 16384, "n-gpu-layers": "auto"},
        }
    }
    rendered = hubctl.render_models_ini(catalog, Path("/models"), 1024)
    assert "[runtime-name]" in rendered
    assert "model = /models/external/registered/model.gguf" in rendered
    assert "ctx-size = 16384" in rendered
    assert "n-gpu-layers = auto" in rendered


def test_catalog_overlay_merge_can_add_and_remove_resources(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "overlay.yaml"
    base.write_text(yaml.safe_dump(base_catalog(), sort_keys=False), encoding="utf-8")
    overlay.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "models": {
                    "remote-model": {
                        "backend": "remote",
                        "capabilities": ["chat"],
                        "artifact": {"kind": "none"},
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    merged = hubctl.load_catalog(base, overlay)
    assert "remote-model" in merged["models"]
    removed = hubctl.merge_mapping(merged, {"models": {"remote-model": None}})
    assert "remote-model" not in removed["models"]


def test_kubernetes_values_include_declared_capabilities_and_storage() -> None:
    catalog = base_catalog()
    catalog["models"] = {
        "cluster": {
            "backend": "local",
            "upstream_model": "cluster-runtime",
            "priority": 80,
            "capabilities": {"chat": {}, "code": {}},
            "features": {"tools": True},
            "artifact": {
                "kind": "pvc",
                "claim_name": "shared-weights",
                "sub_path": "models/cluster.gguf",
            },
            "runtime": {"ctx-size": 32768},
        }
    }
    values = hubctl.catalog_to_k8s_values(catalog)
    assert values["models"]["cluster"]["capabilities"] == {"chat": {}, "code": {}}
    assert values["models"]["cluster"]["features"] == {"tools": True}
    volume_claim = values["llama"]["extraVolumes"][0]["persistentVolumeClaim"]
    assert volume_claim["claimName"] == "shared-weights"
