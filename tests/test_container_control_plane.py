from __future__ import annotations

import importlib.util
import os
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

MODULE_PATH = Path(__file__).resolve().parents[1] / "ops" / "hubctl.py"
SPEC = importlib.util.spec_from_file_location("hubctl_ops", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
hubctl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hubctl)


def base_catalog() -> dict:
    return {
        "version": 1,
        "models": {
            "assistant-model": {
                "display_name": "Assistant",
                "role": "assistant",
                "filename": "assistant.gguf",
                "aliases": ["assistant-alias.gguf"],
                "source": {},
                "runtime": {
                    "ctx-size": 8192,
                    "n-gpu-layers": "auto",
                    "cache-type-k": "q8_0",
                    "cache-type-v": "q8_0",
                },
            }
        },
        "profiles": {
            "assistant": {
                "model": "assistant-model",
                "temperature": 0.5,
            }
        },
    }


def write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_compose_declares_all_runtime_services() -> None:
    compose_path = Path(__file__).resolve().parents[1] / "compose.yaml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = set(compose["services"])
    assert {
        "postgres",
        "config-init",
        "llama",
        "gateway",
        "open-webui",
        "sillytavern",
        "hermes",
        "prometheus",
        "toolbox",
    } <= services
    assert compose["services"]["postgres"]["ports"] == [
        "${BIND_ADDRESS}:${POSTGRES_HOST_PORT}:5432"
    ]
    assert compose["services"]["llama"]["deploy"]["resources"]["reservations"][
        "devices"
    ][0]["capabilities"] == ["gpu"]


def test_compose_optional_services_use_profiles() -> None:
    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "compose.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert compose["services"]["hermes"]["profiles"] == ["agent"]
    assert compose["services"]["prometheus"]["profiles"] == ["observability"]


def test_environment_initialization_generates_unique_high_ports_and_secrets(
    tmp_path: Path,
) -> None:
    template = Path(__file__).resolve().parents[1] / ".env.example"
    output = tmp_path / ".env"
    args = Namespace(
        template=str(template),
        output=str(output),
        reallocate_ports=False,
        rotate_secrets=False,
    )

    assert hubctl.cmd_env_init(args) == 0
    values = hubctl.parse_env(output)
    ports = [int(values[key]) for key in hubctl.PORT_KEYS]
    assert len(ports) == len(set(ports))
    assert all(40000 <= port <= 60999 for port in ports)
    assert all(len(values[key]) >= 32 for key in hubctl.SECRET_KEYS)
    assert output.stat().st_mode & 0o777 == 0o600


def test_environment_init_preserves_existing_values(tmp_path: Path) -> None:
    template = Path(__file__).resolve().parents[1] / ".env.example"
    output = tmp_path / ".env"
    output.write_text(
        "POSTGRES_HOST_PORT=49991\nPOSTGRES_PASSWORD=existing-secret-value\n",
        encoding="utf-8",
    )
    args = Namespace(
        template=str(template),
        output=str(output),
        reallocate_ports=False,
        rotate_secrets=False,
    )

    hubctl.cmd_env_init(args)
    values = hubctl.parse_env(output)
    assert values["POSTGRES_HOST_PORT"] == "49991"
    assert values["POSTGRES_PASSWORD"] == "existing-secret-value"


def test_recursive_overlay_can_change_runtime_without_repeating_model(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    overlay_path = tmp_path / "overlay.yaml"
    write_yaml(catalog_path, base_catalog())
    write_yaml(
        overlay_path,
        {
            "version": 1,
            "models": {
                "assistant-model": {
                    "runtime": {"ctx-size": 16384},
                }
            },
        },
    )

    catalog = hubctl.load_catalog(catalog_path, overlay_path)
    model = catalog["models"]["assistant-model"]
    assert model["filename"] == "assistant.gguf"
    assert model["runtime"]["ctx-size"] == 16384
    assert model["runtime"]["cache-type-k"] == "q8_0"


def test_catalog_rejects_profile_with_unknown_model(tmp_path: Path) -> None:
    catalog = base_catalog()
    catalog["profiles"]["assistant"]["model"] = "missing"
    path = tmp_path / "catalog.yaml"
    write_yaml(path, catalog)
    with pytest.raises(hubctl.HubError, match="unknown model"):
        hubctl.load_catalog(path, None)


def test_renderer_uses_existing_alias_and_creates_runtime_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    overlay_path = tmp_path / "overlay.yaml"
    models_dir = tmp_path / "models"
    runtime_dir = tmp_path / "runtime"
    hermes_dir = tmp_path / "hermes"
    models_dir.mkdir()
    write_yaml(catalog_path, base_catalog())
    (models_dir / "assistant-alias.gguf").write_bytes(b"gguf-test")
    monkeypatch.setenv("LLAMA_API_KEY", "test-llama-key")
    monkeypatch.setenv("LLAMA_FIT_TARGET_MIB", "768")

    args = Namespace(
        catalog=str(catalog_path),
        overlay=str(overlay_path),
        models_dir=str(models_dir),
        runtime_dir=str(runtime_dir),
        hermes_dir=str(hermes_dir),
    )
    assert hubctl.cmd_runtime_render(args) == 0

    rendered = (runtime_dir / "models.ini").read_text(encoding="utf-8")
    assert f"model = {models_dir / 'assistant-alias.gguf'}" in rendered
    assert "fit-target = 768" in rendered
    assert (runtime_dir / "profiles.json").is_file()
    assert (runtime_dir / "catalog.resolved.json").is_file()
    assert (runtime_dir / "llama_api_key").read_text().strip() == "test-llama-key"
    assert (hermes_dir / "config.yaml").is_file()


def test_model_import_normalizes_to_catalog_filename(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    source = tmp_path / "downloaded-name.gguf"
    models_dir = tmp_path / "models"
    write_yaml(catalog_path, base_catalog())
    source.write_bytes(b"model-data")

    args = Namespace(
        catalog=str(catalog_path),
        overlay=None,
        model_id="assistant-model",
        source=str(source),
        models_dir=str(models_dir),
        force=False,
    )
    assert hubctl.cmd_model_import(args) == 0
    assert (models_dir / "assistant.gguf").read_bytes() == b"model-data"
    assert not any(path.name.endswith(".partial") for path in models_dir.iterdir())


def test_model_import_refuses_overwrite_without_force(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    source = tmp_path / "source.gguf"
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    write_yaml(catalog_path, base_catalog())
    source.write_bytes(b"new")
    (models_dir / "assistant.gguf").write_bytes(b"old")

    args = Namespace(
        catalog=str(catalog_path),
        overlay=None,
        model_id="assistant-model",
        source=str(source),
        models_dir=str(models_dir),
        force=False,
    )
    with pytest.raises(hubctl.HubError, match="already exists"):
        hubctl.cmd_model_import(args)
    assert (models_dir / "assistant.gguf").read_bytes() == b"old"


def test_register_writes_private_overlay(tmp_path: Path) -> None:
    overlay = tmp_path / "catalog.local.yaml"
    args = Namespace(
        model_id="private-model",
        overlay=str(overlay),
        filename="Private-Q4_K_M.gguf",
        display_name="Private",
        description="Private test model",
        role="general",
        context=32768,
        gpu_layers="auto",
        cache_type="q8_0",
        repository=None,
        file=None,
        url=None,
    )
    assert hubctl.cmd_model_register(args) == 0
    registered = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    assert registered["models"]["private-model"]["filename"] == "Private-Q4_K_M.gguf"
    assert registered["models"]["private-model"]["runtime"]["ctx-size"] == 32768
    assert overlay.stat().st_mode & 0o777 == 0o600


def test_k8s_values_are_derived_from_catalog(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    output = tmp_path / "models-values.yaml"
    write_yaml(catalog_path, base_catalog())
    args = Namespace(
        catalog=str(catalog_path),
        overlay=None,
        output=str(output),
    )
    assert hubctl.cmd_catalog_k8s_values(args) == 0
    values = yaml.safe_load(output.read_text(encoding="utf-8"))
    model = values["models"]["assistant-model"]
    assert model["filename"] == "assistant.gguf"
    assert model["contextSize"] == 8192
    assert model["cacheTypeK"] == "q8_0"


def test_doctor_rejects_duplicate_ports(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    env_path = tmp_path / ".env"
    write_yaml(catalog_path, base_catalog())
    values = {key: "50001" for key in hubctl.PORT_KEYS}
    values.update({key: "x" * 32 for key in hubctl.SECRET_KEYS})
    env_path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    args = Namespace(
        env_file=str(env_path),
        catalog=str(catalog_path),
        overlay=None,
    )
    assert hubctl.cmd_doctor(args) == 1


def test_model_filename_command_returns_canonical_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    write_yaml(catalog_path, base_catalog())
    args = Namespace(
        catalog=str(catalog_path),
        overlay=None,
        model_id="assistant-model",
    )
    assert hubctl.cmd_model_filename(args) == 0
    assert capsys.readouterr().out.strip() == "assistant.gguf"


def test_helm_chart_contains_gpu_and_clusterip_defaults() -> None:
    chart = Path(__file__).resolve().parents[1] / "deploy" / "helm" / "local-ai-hub"
    values = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
    assert values["llama"]["gpu"]["resourceName"] == "nvidia.com/gpu"
    assert values["llama"]["modelsMax"] == 1
    assert values["llama"]["service"]["type"] == "ClusterIP"
    assert values["hermes"]["enabled"] is False


def test_generated_files_are_not_tracked_by_default() -> None:
    ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(
        encoding="utf-8"
    )
    assert "config/models/catalog.local.yaml" in ignore
    assert "*.gguf" in ignore
    assert "backups/" in ignore
