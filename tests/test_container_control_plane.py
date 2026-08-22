from __future__ import annotations

import importlib.util
import os
import stat
from argparse import Namespace
from pathlib import Path

import yaml


spec = importlib.util.spec_from_file_location("hubctl_container", Path("ops/hubctl.py"))
assert spec and spec.loader
hubctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hubctl)


def test_base_catalog_is_model_neutral() -> None:
    catalog = yaml.safe_load(Path("config/models/catalog.yaml").read_text(encoding="utf-8"))
    assert catalog["version"] == 2
    assert catalog["models"] == {}
    assert catalog["backends"]["local-llama"]["kind"] == "llama.cpp"
    assert catalog["backends"]["local-llama"]["base_url_env"] == "LLAMA_BASE_URL"
    assert {"chat", "code", "story", "image", "agent"}.issubset(
        catalog["experiences"]
    )


def test_compose_has_optional_story_agent_and_observability_profiles() -> None:
    compose = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["sillytavern"]["profiles"] == ["story"]
    assert services["hermes"]["profiles"] == ["agent"]
    assert services["prometheus"]["profiles"] == ["observability"]
    assert services["gateway"]["volumes"][-1] == "runtime-config:/runtime:ro"
    assert "host.docker.internal:host-gateway" in services["gateway"]["extra_hosts"]


def test_hub_is_executable_and_exposes_ai_first_commands() -> None:
    path = Path("hub")
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR
    text = path.read_text(encoding="utf-8")
    for command in (
        "catalog plan",
        "catalog apply",
        "model discover",
        "model link",
        "model register",
        "k8s model-import",
    ):
        assert command in text
    assert "compose.generated.yaml" in text


def test_environment_initialization_allocates_unique_high_ports(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    output = tmp_path / ".env"
    template.write_text(Path(".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    args = Namespace(
        template=str(template),
        output=str(output),
        reallocate_ports=False,
        rotate_secrets=False,
        rotate_postgres_password=False,
    )
    assert hubctl.cmd_env_init(args) == 0
    values = hubctl.parse_env(output)
    ports = [int(values[key]) for key in hubctl.PORT_KEYS]
    assert len(set(ports)) == len(ports)
    assert all(40000 <= port <= 60999 for port in ports)
    assert all(len(values[key]) >= 32 for key in hubctl.SECRET_KEYS)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_environment_initialization_is_stable_without_rotation(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    output = tmp_path / ".env"
    template.write_text(Path(".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    args = Namespace(
        template=str(template),
        output=str(output),
        reallocate_ports=False,
        rotate_secrets=False,
        rotate_postgres_password=False,
    )
    hubctl.cmd_env_init(args)
    first = hubctl.parse_env(output)
    hubctl.cmd_env_init(args)
    second = hubctl.parse_env(output)
    assert first == second


def test_host_file_generates_read_only_bind_mount() -> None:
    catalog = {
        "version": 2,
        "backends": {
            "local": {"kind": "llama.cpp", "base_url": "http://llama:8080"}
        },
        "models": {
            "project-model": {
                "backend": "local",
                "capabilities": ["story"],
                "artifact": {
                    "kind": "host_path",
                    "path": "/home/operator/project/model.gguf",
                    "read_only": True,
                },
            }
        },
        "experiences": {"story": {"capability": "story"}},
    }
    hubctl.validate_catalog(catalog)
    override = hubctl.render_compose_override(catalog)
    mount = override["services"]["llama"]["volumes"][0]
    assert mount == {
        "type": "bind",
        "source": "/home/operator/project/model.gguf",
        "target": "/models/external/project-model/model.gguf",
        "read_only": True,
    }


def test_existing_docker_volume_remains_externally_owned() -> None:
    catalog = {
        "version": 2,
        "backends": {
            "local": {"kind": "llama.cpp", "base_url": "http://llama:8080"}
        },
        "models": {
            "shared-model": {
                "backend": "local",
                "capabilities": ["chat"],
                "artifact": {
                    "kind": "docker_volume",
                    "volume": "shared-model-cache",
                    "sub_path": "model.gguf",
                },
            }
        },
        "experiences": {"chat": {"capability": "chat"}},
    }
    override = hubctl.render_compose_override(catalog)
    assert override["volumes"]["external-shared-model"] == {
        "external": True,
        "name": "shared-model-cache",
    }


def test_remote_model_requires_no_artifact() -> None:
    catalog = {
        "version": 2,
        "backends": {
            "remote": {
                "kind": "openai-compatible",
                "base_url": "http://endpoint/v1",
            }
        },
        "models": {
            "remote-model": {
                "backend": "remote",
                "capabilities": ["chat", "image"],
                "artifact": {"kind": "none"},
            }
        },
        "experiences": {"chat": {"capability": "chat"}},
    }
    hubctl.validate_catalog(catalog)
    assert hubctl.render_compose_override(catalog) == {"services": {"llama": {}}}


def test_local_llama_model_without_artifact_is_rejected() -> None:
    catalog = {
        "version": 2,
        "backends": {
            "local": {"kind": "llama.cpp", "base_url": "http://llama:8080"}
        },
        "models": {
            "invalid": {
                "backend": "local",
                "capabilities": ["chat"],
                "artifact": {"kind": "none"},
            }
        },
        "experiences": {"chat": {"capability": "chat"}},
    }
    try:
        hubctl.validate_catalog(catalog)
    except hubctl.HubError as exc:
        assert "requires a local artifact" in str(exc)
    else:
        raise AssertionError("local model without an artifact was accepted")


def test_kubernetes_values_preserve_full_catalog_and_existing_pvc() -> None:
    catalog = {
        "version": 2,
        "backends": {
            "local": {"kind": "llama.cpp", "base_url_env": "LLAMA_BASE_URL"}
        },
        "models": {
            "cluster-model": {
                "display_name": "Cluster Model",
                "backend": "local",
                "upstream_model": "cluster-runtime",
                "priority": 75,
                "capabilities": {"chat": {}, "code": {}},
                "features": {"tools": True},
                "artifact": {
                    "kind": "pvc",
                    "claim_name": "existing-weights",
                    "sub_path": "text/model.gguf",
                },
                "runtime": {"ctx-size": 32768},
            }
        },
        "experiences": {"code": {"capability": "code"}},
    }
    values = hubctl.catalog_to_k8s_values(catalog)
    assert values["catalog"] == catalog
    assert values["models"]["cluster-model"]["upstreamModel"] == "cluster-runtime"
    assert values["models"]["cluster-model"]["capabilities"] == {
        "chat": {},
        "code": {},
    }
    assert values["llama"]["extraVolumes"][0]["persistentVolumeClaim"] == {
        "claimName": "existing-weights"
    }


def test_schema_examples_are_valid_overlays() -> None:
    base = yaml.safe_load(Path("config/models/catalog.yaml").read_text(encoding="utf-8"))
    for path in sorted(Path("examples/catalog").glob("*.yaml")):
        overlay = yaml.safe_load(path.read_text(encoding="utf-8"))
        merged = hubctl.merge_mapping(base, overlay)
        hubctl.validate_catalog(merged)


def test_generic_distribution_does_not_contain_managed_weights() -> None:
    tracked_candidates = list(Path(".").rglob("*.gguf"))
    assert tracked_candidates == []
    assert os.getenv("GATEWAY_API_KEY") is None or "change-me" not in os.getenv(
        "GATEWAY_API_KEY", ""
    )
