#!/usr/bin/env python3
"""Containerized, AI-friendly control plane for Agent UI 0.3."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any

import requests
import yaml

from local_ai_hub.config import RegistryDocument
from local_ai_hub.profiles import ProfileRegistry

PORT_KEYS = (
    "POSTGRES_HOST_PORT",
    "LLAMA_HOST_PORT",
    "GATEWAY_HOST_PORT",
    "OPEN_WEBUI_HOST_PORT",
    "SILLYTAVERN_HOST_PORT",
    "HERMES_API_HOST_PORT",
    "HERMES_DASHBOARD_HOST_PORT",
    "PROMETHEUS_HOST_PORT",
)
SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "LLAMA_API_KEY",
    "GATEWAY_API_KEY",
    "WEBUI_SECRET_KEY",
    "HERMES_API_KEY",
    "HERMES_DASHBOARD_PASSWORD",
)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class HubError(RuntimeError):
    """Expected operator-facing error."""


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def read_yaml(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    if not path.exists():
        if missing_ok:
            return {}
        raise HubError(f"Required YAML file does not exist: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise HubError(f"Invalid YAML in {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise HubError(f"Expected a YAML mapping in {path}")
    return loaded


def atomic_write(path: Path, content: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    if mode is not None:
        temporary.chmod(mode)
    temporary.replace(path)


def merge_mapping(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_mapping(result[key], value)
        elif value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def load_registry(base_path: Path, overlay_path: Path | None) -> dict[str, Any]:
    merged = read_yaml(base_path)
    if overlay_path is not None and overlay_path.exists():
        merged = merge_mapping(merged, read_yaml(overlay_path, missing_ok=True))
    try:
        document = RegistryDocument.model_validate(merged)
    except Exception as exc:
        raise HubError(f"Registry validation failed: {exc}") from exc
    return document.model_dump(by_alias=True, mode="json", exclude_none=True)


# Compatibility for older tests and automation.
load_catalog = load_registry


def write_overlay(path: Path, value: dict[str, Any]) -> None:
    value.setdefault("version", 2)
    atomic_write(path, yaml.safe_dump(value, sort_keys=False), mode=0o600)


def validate_id(value: str, label: str) -> str:
    if not ID_RE.fullmatch(value):
        raise HubError(f"Invalid {label}: {value}")
    return value


def allocate_port(used: set[int]) -> int:
    for _ in range(5000):
        candidate = random.SystemRandom().randint(40000, 60999)
        if candidate in used:
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            sock.bind(("127.0.0.1", candidate))
        except OSError:
            continue
        finally:
            sock.close()
        used.add(candidate)
        return candidate
    raise HubError("Unable to allocate an unused high TCP port")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def render_env_template(template: Path, values: dict[str, str]) -> str:
    rendered: list[str] = []
    seen: set[str] = set()
    for raw_line in template.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.lstrip().startswith("#") or "=" not in raw_line:
            rendered.append(raw_line)
            continue
        key, _ = raw_line.split("=", 1)
        key = key.strip()
        seen.add(key)
        rendered.append(f"{key}={values.get(key, '')}")
    for key in sorted(set(values) - seen):
        rendered.append(f"{key}={values[key]}")
    return "\n".join(rendered) + "\n"


def cmd_env_init(args: argparse.Namespace) -> int:
    template = Path(args.template)
    output = Path(args.output)
    if not template.exists():
        raise HubError(f"Environment template not found: {template}")
    existing = parse_env(output) if output.exists() else {}
    values = {**parse_env(template), **existing}
    used: set[int] = set()
    for key in PORT_KEYS:
        current = values.get(key, "")
        if current and not args.reallocate_ports:
            try:
                port = int(current)
            except ValueError as exc:
                raise HubError(f"{key} must be an integer") from exc
            if not 1024 <= port <= 65535 or port in used:
                raise HubError(f"Invalid or duplicate configured host port: {key}={port}")
            used.add(port)
        else:
            values[key] = str(allocate_port(used))
    for key in SECRET_KEYS:
        rotate = args.rotate_secrets
        if key == "POSTGRES_PASSWORD" and output.exists():
            rotate = rotate and args.rotate_postgres_password
        if not values.get(key) or rotate:
            values[key] = secrets.token_urlsafe(32)
    atomic_write(output, render_env_template(template, values), mode=0o600)
    result = {"path": str(output), "ports": {key: int(values[key]) for key in PORT_KEYS}}
    print(json.dumps(result, indent=2) if args.json else f"Wrote {output}")
    return 0


def source_mount(registry: dict[str, Any], source_id: str) -> Path:
    try:
        source = registry["sources"][source_id]
    except KeyError as exc:
        raise HubError(f"Unknown source: {source_id}") from exc
    return Path(source["mount_path"])


def model_path(registry: dict[str, Any], model: dict[str, Any]) -> Path | None:
    artifact = model.get("artifact")
    if not artifact:
        return None
    return source_mount(registry, artifact["source"]) / artifact["path"]


def ini_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_models_ini(registry: dict[str, Any], fit_target: int) -> str:
    lines = [
        "version = 1",
        "",
        "[*]",
        "jinja = true",
        "fit = true",
        f"fit-target = {fit_target}",
        "parallel = 1",
        "load-on-startup = false",
        "stop-timeout = 180",
        "dedup-cache-models = true",
    ]
    for model_id, model in registry["models"].items():
        provider = registry["providers"][model["provider"]]
        path = model_path(registry, model)
        if not model.get("enabled", True) or provider["type"] != "llama_cpp" or path is None:
            continue
        lines.extend(("", f"[{model_id}]", f"model = {path}"))
        for key, value in model.get("runtime", {}).items():
            if key == "fit-target":
                value = fit_target
            lines.append(f"{key} = {ini_value(value)}")
    return "\n".join(lines) + "\n"


def compose_mounts(registry: dict[str, Any]) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
    for source_id, source in registry["sources"].items():
        if source["type"] != "host":
            continue
        host_path = source.get("host_path")
        if not host_path:
            raise HubError(f"Host source {source_id!r} is missing host_path")
        path = Path(host_path)
        if not path.is_absolute():
            raise HubError(f"Host source {source_id!r} must use an absolute host_path")
        mounts.append(
            {
                "type": "bind",
                "source": str(path),
                "target": source["mount_path"],
                "read_only": bool(source.get("read_only", True)),
                "bind": {"create_host_path": False},
            }
        )
    return mounts


def render_compose_override(registry: dict[str, Any]) -> str:
    mounts = compose_mounts(registry)
    services = {
        name: {"volumes": mounts}
        for name in ("config-init", "llama", "toolbox")
        if mounts
    }
    return yaml.safe_dump({"services": services}, sort_keys=False)


def render_hermes(registry: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    profile_id = "agent" if "agent" in registry["profiles"] else "chat-deep"
    gateway_key = os.getenv("GATEWAY_API_KEY", "local-only")
    config = {
        "model": {
            "default": profile_id,
            "provider": "custom",
            "base_url": "http://gateway:8000/v1",
            "api_key": gateway_key,
        },
        "tool_loop_guardrails": {
            "hard_stop_enabled": True,
            "hard_stop_after": {"exact_failure": 5, "idempotent_no_progress": 5},
        },
        "database": {"journal_mode": "wal"},
    }
    atomic_write(directory / "config.yaml", yaml.safe_dump(config, sort_keys=False), mode=0o600)
    soul = Path("/opt/agent-ui/config/hermes/SOUL.md")
    if soul.exists():
        atomic_write(directory / "SOUL.md", soul.read_text(encoding="utf-8"), mode=0o600)


def cmd_runtime_render(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry), Path(args.overlay) if args.overlay else None)
    runtime_dir = Path(args.runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    fit_target = int(os.getenv("LLAMA_FIT_TARGET_MIB", "1024"))
    atomic_write(runtime_dir / "models.ini", render_models_ini(registry, fit_target), mode=0o600)
    atomic_write(
        runtime_dir / "registry.json",
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    resolved = json.loads(json.dumps(registry))
    for model in resolved["models"].values():
        path = model_path(registry, model)
        model["resolved_path"] = str(path) if path else None
        model["present"] = bool(path and path.is_file())
        model["size_bytes"] = path.stat().st_size if path and path.is_file() else None
    atomic_write(
        runtime_dir / "registry.resolved.json",
        json.dumps(resolved, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    atomic_write(runtime_dir / "llama_api_key", os.getenv("LLAMA_API_KEY", "") + "\n")
    if args.hermes_dir:
        render_hermes(registry, Path(args.hermes_dir))
    if args.compose_override:
        atomic_write(Path(args.compose_override), render_compose_override(registry), mode=0o600)
    print(json.dumps({"models": len(registry["models"]), "profiles": len(registry["profiles"])}, indent=2))
    return 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(6)}.partial")
    try:
        with source.open("rb") as src, temporary.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def registry_and_model(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = load_registry(Path(args.registry), Path(args.overlay) if args.overlay else None)
    try:
        return registry, registry["models"][args.model_id]
    except KeyError as exc:
        raise HubError(f"Unknown model: {args.model_id}") from exc


def cmd_registry_show(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry), Path(args.overlay) if args.overlay else None)
    print(json.dumps(registry, indent=2) if args.json else yaml.safe_dump(registry, sort_keys=False))
    return 0


def cmd_registry_schema(args: argparse.Namespace) -> int:
    print(json.dumps(RegistryDocument.model_json_schema(by_alias=True), indent=2))
    return 0


def cmd_registry_validate(args: argparse.Namespace) -> int:
    base = Path(args.registry)
    overlay = Path(args.input) if args.input else (Path(args.overlay) if args.overlay else None)
    registry = load_registry(base, overlay)
    print(json.dumps({"valid": True, "providers": len(registry["providers"]), "models": len(registry["models"]), "profiles": len(registry["profiles"])}, indent=2))
    return 0


def cmd_registry_plan(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry), Path(args.overlay) if args.overlay else None)
    document = RegistryDocument.model_validate(registry)
    report = ProfileRegistry(document).capability_report()
    report["sources"] = registry["sources"]
    report["providers"] = {
        key: {"type": value["type"], "enabled": value.get("enabled", True), "base_url": value["base_url"]}
        for key, value in registry["providers"].items()
    }
    print(json.dumps(report, indent=2))
    return 0


def cmd_registry_apply(args: argparse.Namespace) -> int:
    base_path = Path(args.registry)
    overlay_path = Path(args.overlay)
    manifest = read_yaml(Path(args.input))
    existing = {} if args.replace else read_yaml(overlay_path, missing_ok=True)
    candidate_overlay = manifest if args.replace else merge_mapping(existing, manifest)
    candidate_overlay.setdefault("version", 2)
    merged = merge_mapping(read_yaml(base_path), candidate_overlay)
    try:
        RegistryDocument.model_validate(merged)
    except Exception as exc:
        raise HubError(f"Applied registry would be invalid: {exc}") from exc
    if args.dry_run:
        print(yaml.safe_dump(candidate_overlay, sort_keys=False))
        return 0
    write_overlay(overlay_path, candidate_overlay)
    print(json.dumps({"applied": str(args.input), "overlay": str(overlay_path)}, indent=2))
    return 0


def cmd_registry_compose_override(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry), Path(args.overlay) if args.overlay else None)
    content = render_compose_override(registry)
    if args.output:
        atomic_write(Path(args.output), content, mode=0o600)
    else:
        print(content, end="")
    return 0


def cmd_source_list(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry), Path(args.overlay) if args.overlay else None)
    print(json.dumps(registry["sources"], indent=2))
    return 0


def cmd_source_add(args: argparse.Namespace) -> int:
    source_id = validate_id(args.source_id, "source id")
    overlay_path = Path(args.overlay)
    overlay = read_yaml(overlay_path, missing_ok=True)
    overlay.setdefault("version", 2)
    sources = overlay.setdefault("sources", {})
    mount_path = args.mount_path or f"/model-sources/{source_id}"
    source: dict[str, Any] = {
        "type": args.type,
        "description": args.description or "Operator-registered model source.",
        "mount_path": mount_path,
        "read_only": not args.writable,
    }
    if args.type == "host":
        if not args.host_path:
            raise HubError("host sources require --host-path")
        host_path = Path(args.host_path).expanduser().resolve()
        if not host_path.is_dir():
            raise HubError("host source paths must be existing directories")
        source["host_path"] = str(host_path)
    kubernetes: dict[str, Any] = {}
    if args.k8s_existing_claim:
        kubernetes = {"type": "existingClaim", "claimName": args.k8s_existing_claim}
    elif args.k8s_host_path:
        kubernetes = {"type": "hostPath", "path": args.k8s_host_path}
    elif args.k8s_nfs_server and args.k8s_nfs_path:
        kubernetes = {"type": "nfs", "server": args.k8s_nfs_server, "path": args.k8s_nfs_path}
    elif args.k8s_csi_driver:
        kubernetes = {"type": "csi", "driver": args.k8s_csi_driver}
    if kubernetes:
        source["kubernetes"] = kubernetes
    sources[source_id] = source
    write_overlay(overlay_path, overlay)
    load_registry(Path(args.registry), overlay_path)
    print(json.dumps({source_id: source}, indent=2))
    return 0


def cmd_source_remove(args: argparse.Namespace) -> int:
    overlay_path = Path(args.overlay)
    overlay = read_yaml(overlay_path, missing_ok=True)
    overlay.get("sources", {}).pop(args.source_id, None)
    write_overlay(overlay_path, overlay)
    print(json.dumps({"removed": args.source_id}))
    return 0


def parse_pairs(values: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise HubError(f"Expected KEY=VALUE, got {value!r}")
        key, raw = value.split("=", 1)
        result[key] = yaml.safe_load(raw)
    return result


def split_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(item.strip() for item in value.split(",") if item.strip())
    return list(dict.fromkeys(result))


def cmd_provider_add(args: argparse.Namespace) -> int:
    provider_id = validate_id(args.provider_id, "provider id")
    overlay_path = Path(args.overlay)
    overlay = read_yaml(overlay_path, missing_ok=True)
    overlay.setdefault("version", 2)
    provider: dict[str, Any] = {
        "type": args.type,
        "enabled": not args.disabled,
        "required": args.required,
        "description": args.description or "Operator-registered inference provider.",
        "base_url": args.base_url,
        "endpoints": parse_pairs(args.endpoint),
        "max_concurrency": args.max_concurrency,
    }
    if args.control_url:
        provider["control_url"] = args.control_url
    if args.api_key_env:
        provider["api_key_env"] = args.api_key_env
    if args.resource_group:
        provider["resource_group"] = args.resource_group
    overlay.setdefault("providers", {})[provider_id] = provider
    write_overlay(overlay_path, overlay)
    load_registry(Path(args.registry), overlay_path)
    print(json.dumps({provider_id: provider}, indent=2))
    return 0


def cmd_model_list(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry), Path(args.overlay) if args.overlay else None)
    records: list[dict[str, Any]] = []
    for model_id, model in registry["models"].items():
        path = model_path(registry, model)
        records.append(
            {
                "id": model_id,
                "provider": model["provider"],
                "capabilities": model.get("capabilities", []),
                "source": model.get("artifact", {}).get("source"),
                "path": str(path) if path else None,
                "present": bool(path and path.is_file()),
            }
        )
    print(json.dumps(records, indent=2))
    return 0


def cmd_model_register(args: argparse.Namespace) -> int:
    model_id = validate_id(args.model_id, "model id")
    base_path = Path(args.registry)
    overlay_path = Path(args.overlay)
    current = load_registry(base_path, overlay_path if overlay_path.exists() else None)
    if args.provider not in current["providers"]:
        raise HubError(f"Unknown provider: {args.provider}")
    overlay = read_yaml(overlay_path, missing_ok=True)
    overlay.setdefault("version", 2)
    artifact: dict[str, str] | None = None
    if args.host_path:
        host_file = Path(args.host_path).expanduser().resolve()
        if not host_file.is_file():
            raise HubError(f"Model file does not exist: {host_file}")
        source_id = args.source or f"host-{model_id}"
        validate_id(source_id, "source id")
        overlay.setdefault("sources", {})[source_id] = {
            "type": "host",
            "description": f"Host directory for {model_id}.",
            "mount_path": f"/model-sources/{source_id}",
            "host_path": str(host_file.parent),
            "read_only": True,
        }
        artifact = {"source": source_id, "path": host_file.name}
    elif args.source or args.path:
        if not args.source or not args.path:
            raise HubError("--source and --path must be supplied together")
        if args.source not in current["sources"] and args.source not in overlay.get("sources", {}):
            raise HubError(f"Unknown source: {args.source}")
        artifact = {"source": args.source, "path": args.path}

    reasoning = {
        "transport": args.reasoning_transport,
        "levels": split_values(args.reasoning_level),
        "field": args.reasoning_field,
    }
    model: dict[str, Any] = {
        "provider": args.provider,
        "display_name": args.display_name or model_id,
        "description": args.description or "Operator-registered inference model.",
        "enabled": not args.disabled,
        "priority": args.priority,
        "capabilities": split_values(args.capability),
        "tags": split_values(args.tag),
        "runtime": parse_pairs(args.runtime),
        "features": {
            "developer_role": args.developer_role,
            "tool_calling": args.tool_calling,
            "streaming": not args.no_streaming,
            "reasoning": reasoning,
        },
    }
    if args.upstream_model:
        model["upstream_model"] = args.upstream_model
    if args.coordinator_model:
        model["coordinator_model"] = args.coordinator_model
    if artifact:
        model["artifact"] = artifact
    overlay.setdefault("models", {})[model_id] = model
    if args.bind_profile:
        if args.bind_profile not in current["profiles"]:
            raise HubError(f"Unknown profile: {args.bind_profile}")
        overlay.setdefault("profiles", {}).setdefault(args.bind_profile, {})["model"] = model_id
    write_overlay(overlay_path, overlay)
    load_registry(base_path, overlay_path)
    print(json.dumps({model_id: model}, indent=2))
    return 0


def cmd_model_import(args: argparse.Namespace) -> int:
    registry, model = registry_and_model(args)
    path = model_path(registry, model)
    if path is None:
        raise HubError("Model has no artifact registration")
    source_id = model["artifact"]["source"]
    if registry["sources"][source_id]["type"] != "managed":
        raise HubError("Import is only for managed sources; host sources are used in place")
    source = Path(args.source)
    if not source.is_file():
        raise HubError(f"Source file does not exist: {source}")
    if path.exists() and not args.force:
        raise HubError(f"Destination already exists: {path}; pass --force")
    copy_atomic(source, path)
    print(json.dumps({"path": str(path), "sha256": sha256_file(path)}, indent=2))
    return 0


def download_url(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(6)}.partial")
    try:
        with requests.get(url, stream=True, timeout=(20, 600)) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                    if chunk:
                        handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def cmd_model_fetch(args: argparse.Namespace) -> int:
    registry, model = registry_and_model(args)
    path = model_path(registry, model)
    if path is None:
        raise HubError("Model has no artifact registration")
    source_id = model["artifact"]["source"]
    if registry["sources"][source_id]["type"] != "managed":
        raise HubError("Fetch is only for managed sources")
    if path.exists() and not args.force:
        raise HubError(f"Destination already exists: {path}; pass --force")
    if args.url:
        download_url(args.url, path)
    elif args.repository and args.file:
        from huggingface_hub import hf_hub_download

        downloaded = Path(
            hf_hub_download(
                repo_id=args.repository,
                filename=args.file,
                local_dir=path.parent / ".downloads",
                token=os.getenv("HF_TOKEN") or None,
            )
        )
        copy_atomic(downloaded, path)
    else:
        raise HubError("Supply --url or both --repository and --file")
    print(json.dumps({"path": str(path), "sha256": sha256_file(path)}, indent=2))
    return 0


def cmd_model_verify(args: argparse.Namespace) -> int:
    registry, model = registry_and_model(args)
    path = model_path(registry, model)
    if path is None or not path.is_file():
        raise HubError(f"Model artifact is not present: {args.model_id}")
    actual = sha256_file(path)
    if args.sha256 and not secrets.compare_digest(args.sha256.lower(), actual.lower()):
        raise HubError(f"Checksum mismatch: expected {args.sha256}, got {actual}")
    print(json.dumps({"path": str(path), "size_bytes": path.stat().st_size, "sha256": actual}, indent=2))
    return 0


def cmd_model_remove(args: argparse.Namespace) -> int:
    overlay_path = Path(args.overlay)
    overlay = read_yaml(overlay_path, missing_ok=True)
    overlay.get("models", {}).pop(args.model_id, None)
    for profile in overlay.get("profiles", {}).values():
        if isinstance(profile, dict) and profile.get("model") == args.model_id:
            profile.pop("model", None)
    write_overlay(overlay_path, overlay)
    print(json.dumps({"removed": args.model_id}))
    return 0


def cmd_profile_bind(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry), Path(args.overlay) if Path(args.overlay).exists() else None)
    if args.profile_id not in registry["profiles"]:
        raise HubError(f"Unknown profile: {args.profile_id}")
    if args.model_id not in registry["models"]:
        raise HubError(f"Unknown model: {args.model_id}")
    overlay_path = Path(args.overlay)
    overlay = read_yaml(overlay_path, missing_ok=True)
    overlay.setdefault("version", 2)
    overlay.setdefault("profiles", {}).setdefault(args.profile_id, {})["model"] = args.model_id
    write_overlay(overlay_path, overlay)
    load_registry(Path(args.registry), overlay_path)
    print(json.dumps({"profile": args.profile_id, "model": args.model_id}))
    return 0


def cmd_profile_unbind(args: argparse.Namespace) -> int:
    overlay_path = Path(args.overlay)
    overlay = read_yaml(overlay_path, missing_ok=True)
    profile = overlay.get("profiles", {}).get(args.profile_id)
    if isinstance(profile, dict):
        profile.pop("model", None)
    write_overlay(overlay_path, overlay)
    print(json.dumps({"profile": args.profile_id, "model": None}))
    return 0


def cmd_registry_k8s_values(args: argparse.Namespace) -> int:
    registry = load_registry(Path(args.registry), Path(args.overlay) if args.overlay else None)
    referenced_sources = {
        model["artifact"]["source"]
        for model in registry["models"].values()
        if model.get("enabled", True)
        and model.get("artifact")
        and registry["providers"][model["provider"]]["type"] == "llama_cpp"
    }
    sources: dict[str, Any] = {}
    for source_id, source in registry["sources"].items():
        if source["type"] == "managed":
            sources[source_id] = {
                "type": "pvc",
                "mountPath": source["mount_path"],
                "existingClaim": "",
                "readOnly": bool(source.get("read_only", False)),
            }
            continue
        kubernetes = dict(source.get("kubernetes") or {})
        if not kubernetes:
            if source_id in referenced_sources:
                raise HubError(
                    f"source {source_id!r} is used by a local model but has no kubernetes mapping"
                )
            continue
        kubernetes["mountPath"] = source["mount_path"]
        kubernetes["readOnly"] = bool(source.get("read_only", True))
        sources[source_id] = kubernetes
    models: dict[str, Any] = {}
    for model_id, model in registry["models"].items():
        provider = registry["providers"][model["provider"]]
        path = model_path(registry, model)
        if provider["type"] != "llama_cpp" or path is None:
            continue
        models[model_id] = {
            "enabled": bool(model.get("enabled", True)),
            "containerPath": str(path),
            "runtime": model.get("runtime", {}),
        }
    values = {"hubRegistry": registry, "modelSources": sources, "models": models}
    output = yaml.safe_dump(values, sort_keys=False)
    if args.output:
        atomic_write(Path(args.output), output, mode=0o600)
    else:
        print(output, end="")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    failures: list[str] = []
    env = parse_env(Path(args.env_file))
    seen: dict[int, str] = {}
    for key in PORT_KEYS:
        try:
            port = int(env.get(key, ""))
        except ValueError:
            failures.append(f"{key} is missing or not an integer")
            continue
        if port in seen:
            failures.append(f"{key} duplicates {seen[port]} on port {port}")
        seen[port] = key
    for key in SECRET_KEYS:
        if len(env.get(key, "")) < 16:
            failures.append(f"{key} is missing or too short")
    try:
        registry = load_registry(Path(args.registry), Path(args.overlay) if args.overlay else None)
        compose_mounts(registry)
    except HubError as exc:
        failures.append(str(exc))
        registry = None
    if failures:
        for failure in failures:
            eprint(f"FAIL: {failure}")
        return 1
    print(json.dumps({"status": "ok", "providers": len(registry["providers"]), "models": len(registry["models"]), "profiles": len(registry["profiles"])}, indent=2))
    return 0


def add_registry_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--registry", default="/opt/agent-ui/config/registry.yaml")
    parser.add_argument("--overlay", default="/state/registry.local.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-ui-ctl")
    groups = parser.add_subparsers(dest="group", required=True)

    env_parser = groups.add_parser("env")
    env_sub = env_parser.add_subparsers(dest="command", required=True)
    env_init = env_sub.add_parser("init")
    env_init.add_argument("--template", default="/workspace/.env.example")
    env_init.add_argument("--output", default="/workspace/.env")
    env_init.add_argument("--reallocate-ports", action="store_true")
    env_init.add_argument("--rotate-secrets", action="store_true")
    env_init.add_argument("--rotate-postgres-password", action="store_true")
    env_init.add_argument("--json", action="store_true")
    env_init.set_defaults(handler=cmd_env_init)

    runtime = groups.add_parser("runtime")
    runtime_sub = runtime.add_subparsers(dest="command", required=True)
    render = runtime_sub.add_parser("render")
    add_registry_args(render)
    render.add_argument("--runtime-dir", default="/runtime")
    render.add_argument("--hermes-dir")
    render.add_argument("--compose-override")
    render.set_defaults(handler=cmd_runtime_render)

    registry = groups.add_parser("registry")
    registry_sub = registry.add_subparsers(dest="command", required=True)
    for name, handler in (("show", cmd_registry_show), ("plan", cmd_registry_plan)):
        item = registry_sub.add_parser(name)
        add_registry_args(item)
        item.add_argument("--json", action="store_true")
        item.set_defaults(handler=handler)
    schema = registry_sub.add_parser("schema")
    schema.set_defaults(handler=cmd_registry_schema)
    validate = registry_sub.add_parser("validate")
    add_registry_args(validate)
    validate.add_argument("--input")
    validate.set_defaults(handler=cmd_registry_validate)
    apply = registry_sub.add_parser("apply")
    add_registry_args(apply)
    apply.add_argument("input")
    apply.add_argument("--replace", action="store_true")
    apply.add_argument("--dry-run", action="store_true")
    apply.set_defaults(handler=cmd_registry_apply)
    override = registry_sub.add_parser("compose-override")
    add_registry_args(override)
    override.add_argument("--output")
    override.set_defaults(handler=cmd_registry_compose_override)
    k8s_values = registry_sub.add_parser("k8s-values")
    add_registry_args(k8s_values)
    k8s_values.add_argument("--output")
    k8s_values.set_defaults(handler=cmd_registry_k8s_values)

    source = groups.add_parser("source")
    source_sub = source.add_subparsers(dest="command", required=True)
    source_list = source_sub.add_parser("list")
    add_registry_args(source_list)
    source_list.set_defaults(handler=cmd_source_list)
    source_add = source_sub.add_parser("add")
    add_registry_args(source_add)
    source_add.add_argument("source_id")
    source_add.add_argument("--type", choices=["managed", "host"], default="host")
    source_add.add_argument("--host-path")
    source_add.add_argument("--mount-path")
    source_add.add_argument("--description")
    source_add.add_argument("--writable", action="store_true")
    source_add.add_argument("--k8s-existing-claim")
    source_add.add_argument("--k8s-host-path")
    source_add.add_argument("--k8s-nfs-server")
    source_add.add_argument("--k8s-nfs-path")
    source_add.add_argument("--k8s-csi-driver")
    source_add.set_defaults(handler=cmd_source_add)
    source_remove = source_sub.add_parser("remove")
    add_registry_args(source_remove)
    source_remove.add_argument("source_id")
    source_remove.set_defaults(handler=cmd_source_remove)

    provider = groups.add_parser("provider")
    provider_sub = provider.add_subparsers(dest="command", required=True)
    provider_add = provider_sub.add_parser("add")
    add_registry_args(provider_add)
    provider_add.add_argument("provider_id")
    provider_add.add_argument("--type", choices=["llama_cpp", "openai_compatible"], required=True)
    provider_add.add_argument("--base-url", required=True)
    provider_add.add_argument("--control-url")
    provider_add.add_argument("--api-key-env")
    provider_add.add_argument("--endpoint", action="append", default=[])
    provider_add.add_argument("--resource-group")
    provider_add.add_argument("--max-concurrency", type=int, default=1)
    provider_add.add_argument("--required", action="store_true")
    provider_add.add_argument("--disabled", action="store_true")
    provider_add.add_argument("--description")
    provider_add.set_defaults(handler=cmd_provider_add)

    model = groups.add_parser("model")
    model_sub = model.add_subparsers(dest="command", required=True)
    model_list = model_sub.add_parser("list")
    add_registry_args(model_list)
    model_list.set_defaults(handler=cmd_model_list)
    model_register = model_sub.add_parser("register")
    add_registry_args(model_register)
    model_register.add_argument("model_id")
    model_register.add_argument("--provider", required=True)
    model_register.add_argument("--capability", action="append", default=[])
    model_register.add_argument("--tag", action="append", default=[])
    model_register.add_argument("--source")
    model_register.add_argument("--path")
    model_register.add_argument("--host-path")
    model_register.add_argument("--upstream-model")
    model_register.add_argument("--coordinator-model")
    model_register.add_argument("--display-name")
    model_register.add_argument("--description")
    model_register.add_argument("--priority", type=int, default=0)
    model_register.add_argument("--runtime", action="append", default=[])
    model_register.add_argument("--reasoning-transport", choices=["none", "flat", "object", "chat_template"], default="none")
    model_register.add_argument("--reasoning-field", default="reasoning_effort")
    model_register.add_argument("--reasoning-level", action="append", default=[])
    model_register.add_argument("--developer-role", action="store_true")
    model_register.add_argument("--tool-calling", action="store_true")
    model_register.add_argument("--no-streaming", action="store_true")
    model_register.add_argument("--disabled", action="store_true")
    model_register.add_argument("--bind-profile")
    model_register.set_defaults(handler=cmd_model_register)
    for name, handler in (("import", cmd_model_import), ("verify", cmd_model_verify), ("fetch", cmd_model_fetch)):
        item = model_sub.add_parser(name)
        add_registry_args(item)
        item.add_argument("model_id")
        if name == "import":
            item.add_argument("--source", required=True)
            item.add_argument("--force", action="store_true")
        elif name == "verify":
            item.add_argument("--sha256")
        else:
            item.add_argument("--url")
            item.add_argument("--repository")
            item.add_argument("--file")
            item.add_argument("--force", action="store_true")
        item.set_defaults(handler=handler)
    model_remove = model_sub.add_parser("remove")
    add_registry_args(model_remove)
    model_remove.add_argument("model_id")
    model_remove.set_defaults(handler=cmd_model_remove)

    profile = groups.add_parser("profile")
    profile_sub = profile.add_subparsers(dest="command", required=True)
    bind = profile_sub.add_parser("bind")
    add_registry_args(bind)
    bind.add_argument("profile_id")
    bind.add_argument("model_id")
    bind.set_defaults(handler=cmd_profile_bind)
    unbind = profile_sub.add_parser("unbind")
    add_registry_args(unbind)
    unbind.add_argument("profile_id")
    unbind.set_defaults(handler=cmd_profile_unbind)

    doctor = groups.add_parser("doctor")
    doctor.add_argument("--env-file", default="/workspace/.env")
    add_registry_args(doctor)
    doctor.set_defaults(handler=cmd_doctor)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.handler(args))
    except HubError as exc:
        eprint(f"error: {exc}")
        return 2
    except requests.RequestException as exc:
        eprint(f"network error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
