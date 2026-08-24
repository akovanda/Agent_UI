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
    assert {"chat", "code", "story", "image", "agent"}.issubset(catalog["experiences"])


def test_compose_has_optional_story_agent_and_observability_profiles() -> None:
    compose = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["sillytavern"]["profiles"] == ["story"]
    assert services["hermes"]["profiles"] == ["agent"]
    assert services["prometheus"]["profiles"] == ["observability"]
    assert services["gateway"]["volumes"][-1] == "runtime-config:/runtime:ro"
    assert "host.docker.internal:host-gateway" in services["gateway"]["extra_hosts"]
    assert services["config-init"]["environment"]["RUNTIME_CONFIG_UID"] == (
        "${RUNTIME_CONFIG_UID:-10001}"
    )
    assert services["config-init"]["environment"]["RUNTIME_CONFIG_GID"] == (
        "${RUNTIME_CONFIG_GID:-10001}"
    )


def test_compose_has_opt_in_private_searxng_web_search() -> None:
    compose = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    searxng = services["searxng"]
    webui = services["open-webui"]

    assert searxng["profiles"] == ["web-search"]
    assert searxng["networks"] == ["agent-ui-tools"]
    assert "ports" not in searxng
    assert searxng["volumes"][0] == "./config/searxng/settings.yml:/etc/searxng/settings.yml:ro"
    assert searxng["healthcheck"]["test"][-1].endswith("/healthz")
    assert "ALL" in searxng["cap_drop"]
    assert "no-new-privileges:true" in searxng["security_opt"]

    assert webui["environment"]["ENABLE_WEB_SEARCH"] == ("${OPEN_WEBUI_ENABLE_WEB_SEARCH:-false}")
    assert webui["environment"]["WEB_SEARCH_ENGINE"] == "searxng"
    assert webui["environment"]["SEARXNG_QUERY_URL"] == "http://searxng:8080/search"
    assert webui["depends_on"]["searxng"] == {
        "condition": "service_healthy",
        "required": False,
    }
    assert webui["networks"] == ["agent-ui-backend", "agent-ui-tools"]

    settings = yaml.safe_load(Path("config/searxng/settings.yml").read_text(encoding="utf-8"))
    assert set(settings["use_default_settings"]["engines"]["remove"]) == {
        "ahmia",
        "torch",
        "wikidata",
    }
    assert "json" in settings["search"]["formats"]
    assert settings["server"]["public_instance"] is False

    env = hubctl.parse_env(Path(".env.example"))
    assert env["OPEN_WEBUI_ENABLE_WEB_SEARCH"] == "false"
    assert env["SEARXNG_IMAGE"].startswith("docker.io/searxng/searxng:2026.8.22-9fea41204@sha256:")


def test_compose_has_isolated_open_terminal_and_builtin_tool_defaults() -> None:
    compose = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    terminal = services["open-terminal"]
    webui = services["open-webui"]

    assert terminal["profiles"] == ["terminal"]
    assert terminal["networks"] == ["agent-ui-tools"]
    assert "ports" not in terminal
    assert terminal["volumes"] == ["open-terminal-data:/home/user"]
    assert terminal["pids_limit"] == 256
    assert terminal["mem_limit"] == "2g"
    assert terminal["cpus"] == 2.0
    assert "ALL" in terminal["cap_drop"]
    assert "NET_ADMIN" in terminal["cap_add"]
    assert "NET_RAW" in terminal["cap_add"]
    assert "no-new-privileges:true" in terminal["security_opt"]
    assert terminal["healthcheck"]["test"][-1].endswith("/api/config")
    assert terminal["environment"]["OPEN_TERMINAL_ENABLE_NOTEBOOKS"] == "false"
    assert terminal["environment"]["OPEN_TERMINAL_CORS_ALLOWED_ORIGINS"] == (
        "http://open-webui:8080"
    )
    assert "Docker socket" in terminal["environment"]["OPEN_TERMINAL_INFO"]

    assert webui["depends_on"]["open-terminal"] == {
        "condition": "service_healthy",
        "required": False,
    }
    assert webui["environment"]["TERMINAL_SERVER_CONNECTIONS"] == (
        "${OPEN_WEBUI_TERMINAL_SERVER_CONNECTIONS:-[]}"
    )
    assert webui["environment"]["CODE_INTERPRETER_ENGINE"] == (
        "${OPEN_WEBUI_CODE_INTERPRETER_ENGINE:-pyodide}"
    )
    assert webui["environment"]["ENABLE_NOTES"] == "${OPEN_WEBUI_ENABLE_NOTES:-true}"

    env = hubctl.parse_env(Path(".env.example"))
    assert env["OPEN_TERMINAL_IMAGE"].startswith("ghcr.io/open-webui/open-terminal:slim@sha256:")
    assert env["OPEN_WEBUI_ENABLE_CODE_INTERPRETER"] == "true"


def test_hub_web_search_option_enables_both_profile_and_open_webui() -> None:
    hub_script = Path("hub").read_text(encoding="utf-8")

    assert "--web-search) web_search=true" in hub_script
    assert '[[ "$web_search" == true ]] && profiles+=(--profile web-search)' in hub_script
    assert 'OPEN_WEBUI_ENABLE_WEB_SEARCH="$web_search_setting"' in hub_script
    assert "SEARXNG_SECRET_KEY is missing or too short" in hub_script


def test_hub_tool_bundle_enables_terminal_search_and_memories() -> None:
    hub_script = Path("hub").read_text(encoding="utf-8")

    assert "--terminal) terminal=true" in hub_script
    assert "--tools) web_search=true; terminal=true; tool_bundle=true" in hub_script
    assert "profiles+=(--profile terminal)" in hub_script
    assert "OPEN_TERMINAL_API_KEY is missing or too short" in hub_script
    assert '[[ "$tool_bundle" == true ]] && memories_setting=true' in hub_script
    assert "http://open-terminal:8000" in hub_script


def test_catalog_apply_does_not_force_a_profiled_llama_service() -> None:
    hub_script = Path("hub").read_text(encoding="utf-8")
    apply_block = hub_script.split("apply_runtime_if_running() {", maxsplit=1)[1].split(
        "ensure_initialized() {", maxsplit=1
    )[0]

    assert "compose config --services" in apply_block
    assert "local targets=(config-init gateway)" in apply_block
    assert "targets=(config-init llama gateway)" in apply_block
    assert 'compose up -d --build --force-recreate "${targets[@]}"' in apply_block
    assert "compose up -d --build --force-recreate config-init llama gateway" not in apply_block


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


def test_smoke_check_is_catalog_aware_for_external_only_installations() -> None:
    smoke = Path("ops/smoke.sh").read_text(encoding="utf-8")

    assert "/runtime/catalog.resolved.json" in smoke
    assert '.kind == "llama.cpp"' in smoke
    assert "skipping its health check" in smoke


def test_toolbox_runtime_tree_is_readable_by_arbitrary_users() -> None:
    dockerfile = Path("deploy/docker/toolbox.Dockerfile").read_text(encoding="utf-8")
    assert "chmod -R a+rX /opt/agent-ui" in dockerfile
    assert "chmod 0755 /opt/agent-ui/ops/hubctl.py /opt/agent-ui/ops/*.sh" in dockerfile


def test_hub_makes_shipped_read_only_bind_mounts_container_readable() -> None:
    hub_script = Path("hub").read_text(encoding="utf-8")

    assert '"$ROOT/config/postgres-init"' in hub_script
    assert '"$ROOT/config/prometheus"' in hub_script
    assert '"$ROOT/config/searxng"' in hub_script
    assert "chmod -R a+rX" in hub_script


def test_generated_local_state_is_excluded_from_git_and_images() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8").splitlines()
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".agent-ui/" in gitignore
    assert ".agent-ui" in dockerignore
    assert ".coverage" in dockerignore


def test_helm_templates_use_values_that_exist_in_the_shipped_chart() -> None:
    chart_dir = Path("deploy/helm/local-ai-hub")
    values = yaml.safe_load((chart_dir / "values.yaml").read_text(encoding="utf-8"))
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (chart_dir / "templates").iterdir()
        if path.is_file()
    )

    assert ".Values.postgresql" not in templates
    assert ".Values.global" not in templates
    assert "signupEnabled" not in templates
    assert "memoriesEnabled" not in templates
    assert 'index .Values.models "gpt-oss-20b"' not in templates
    assert ".Values.postgres.image.pullPolicy" in templates
    assert ".Values.openWebUI.image.pullPolicy" in templates
    assert ".Values.sillyTavern.image.pullPolicy" in templates
    assert ".Values.hermes.image.pullPolicy" in templates
    assert ".Values.openWebUI.enableSignup" in templates
    assert ".Values.openWebUI.enableMemories" in templates
    assert "kind: Secret" in templates

    for component in ("postgres", "llama", "openWebUI", "sillyTavern", "hermes"):
        assert values[component]["storage"]["accessModes"]
        assert "storageClass" in values[component]["storage"]
    for component in ("postgres", "llama", "gateway", "openWebUI", "sillyTavern", "hermes"):
        assert values[component]["service"]["type"] == "ClusterIP"


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
    assert all(len(values[key]) >= 32 for key in hubctl.OPTIONAL_SECRET_KEYS)
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


def test_runtime_compose_override_is_host_readable(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    compose_output = tmp_path / ".agent-ui" / "compose.generated.yaml"
    args = Namespace(
        catalog="config/models/catalog.yaml",
        overlay=str(tmp_path / "missing-overlay.yaml"),
        models_dir=str(tmp_path / "models"),
        runtime_dir=str(runtime_dir),
        compose_output=str(compose_output),
        hermes_dir=None,
    )

    assert hubctl.cmd_runtime_render(args) == 0
    assert stat.S_IMODE(compose_output.stat().st_mode) == 0o644
    assert stat.S_IMODE((runtime_dir / "catalog.resolved.json").stat().st_mode) == 0o600


def test_runtime_render_assigns_configured_service_ownership(tmp_path: Path, monkeypatch) -> None:
    ownership: list[tuple[str, int, int]] = []
    monkeypatch.setenv("RUNTIME_CONFIG_UID", "10001")
    monkeypatch.setenv("RUNTIME_CONFIG_GID", "10002")
    monkeypatch.setattr(
        hubctl.os,
        "chown",
        lambda path, uid, gid: ownership.append((Path(path).name, uid, gid)),
    )
    args = Namespace(
        catalog="config/models/catalog.yaml",
        overlay=str(tmp_path / "missing-overlay.yaml"),
        models_dir=str(tmp_path / "models"),
        runtime_dir=str(tmp_path / "runtime"),
        compose_output=None,
        hermes_dir=None,
    )

    assert hubctl.cmd_runtime_render(args) == 0
    assert ownership == [
        ("models.ini", 10001, 10002),
        ("catalog.resolved.json", 10001, 10002),
        ("profiles.json", 10001, 10002),
        ("llama_api_key", 10001, 10002),
    ]


def test_host_file_generates_read_only_bind_mount() -> None:
    catalog = {
        "version": 2,
        "backends": {"local": {"kind": "llama.cpp", "base_url": "http://llama:8080"}},
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
        "backends": {"local": {"kind": "llama.cpp", "base_url": "http://llama:8080"}},
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
    assert hubctl.render_compose_override(catalog) == {
        "services": {
            "llama": {"profiles": ["local-llama"]},
            "gateway": {
                "depends_on": {"llama": {"condition": "service_started", "required": False}}
            },
        }
    }


def test_local_llama_model_keeps_the_inference_service_enabled() -> None:
    catalog = {
        "version": 2,
        "backends": {"local": {"kind": "llama.cpp", "base_url": "http://llama:8080"}},
        "models": {
            "local-model": {
                "backend": "local",
                "capabilities": ["chat"],
                "artifact": {"kind": "managed"},
            }
        },
        "experiences": {"chat": {"capability": "chat"}},
    }

    override = hubctl.render_compose_override(catalog)
    assert "profiles" not in override["services"]["llama"]
    assert "gateway" not in override["services"]


def test_local_llama_model_without_artifact_is_rejected() -> None:
    catalog = {
        "version": 2,
        "backends": {"local": {"kind": "llama.cpp", "base_url": "http://llama:8080"}},
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
        "backends": {"local": {"kind": "llama.cpp", "base_url_env": "LLAMA_BASE_URL"}},
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
