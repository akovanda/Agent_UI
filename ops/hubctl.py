#!/usr/bin/env python3
"""Containerized control plane for Agent UI.

The host contract is Docker, Docker Compose, and a POSIX shell. The catalog is
intentionally declarative so a person or an automation agent can register local
weights, existing volumes, Kubernetes storage, or remote inference endpoints
without editing application code.
"""

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
RESOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


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


def validate_id(value: str, kind: str) -> None:
    if not RESOURCE_ID_RE.fullmatch(value):
        raise HubError(f"Invalid {kind} id: {value}")


def validate_catalog(catalog: dict[str, Any]) -> None:
    if catalog.get("version") != 2:
        raise HubError("Catalog must declare version: 2")
    for section in ("backends", "models", "experiences"):
        value = catalog.get(section, {})
        if not isinstance(value, dict):
            raise HubError(f"Catalog section {section} must be a mapping")

    backends = catalog.get("backends", {})
    for backend_id, backend in backends.items():
        validate_id(str(backend_id), "backend")
        if not isinstance(backend, dict):
            raise HubError(f"Backend {backend_id} must be a mapping")
        if backend.get("kind") not in {"llama.cpp", "openai-compatible", "comfyui"}:
            raise HubError(f"Backend {backend_id} has unsupported kind {backend.get('kind')!r}")

    models = catalog.get("models", {})
    for model_id, model in models.items():
        validate_id(str(model_id), "model")
        if not isinstance(model, dict):
            raise HubError(f"Model {model_id} must be a mapping")
        backend_id = model.get("backend")
        if backend_id not in backends:
            raise HubError(f"Model {model_id} references unknown backend {backend_id!r}")
        capabilities = model.get("capabilities", {})
        if isinstance(capabilities, list):
            capabilities = {str(item): {} for item in capabilities}
            model["capabilities"] = capabilities
        if not isinstance(capabilities, dict):
            raise HubError(f"Model {model_id} capabilities must be a mapping or list")
        for capability in capabilities:
            if not CAPABILITY_RE.fullmatch(str(capability)):
                raise HubError(f"Model {model_id} has invalid capability {capability!r}")
        artifact = model.get("artifact") or {"kind": "none"}
        if not isinstance(artifact, dict):
            raise HubError(f"Model {model_id} artifact must be a mapping")
        kind = artifact.get("kind", "none")
        if kind not in {
            "managed",
            "host_path",
            "docker_volume",
            "container_path",
            "pvc",
            "hostPath",
            "none",
        }:
            raise HubError(f"Model {model_id} has unsupported artifact kind {kind!r}")
        if kind in {"host_path", "container_path", "hostPath"} and not artifact.get("path"):
            raise HubError(f"Model {model_id} {kind} artifact requires path")
        if kind == "docker_volume" and not artifact.get("volume"):
            raise HubError(f"Model {model_id} docker_volume artifact requires volume")
        if kind == "pvc" and not artifact.get("claim_name"):
            raise HubError(f"Model {model_id} pvc artifact requires claim_name")

    for experience_id, experience in catalog.get("experiences", {}).items():
        validate_id(str(experience_id), "experience")
        if not isinstance(experience, dict):
            raise HubError(f"Experience {experience_id} must be a mapping")
        pinned = experience.get("model")
        capability = experience.get("capability")
        if pinned and pinned not in models:
            raise HubError(f"Experience {experience_id} references unknown model {pinned!r}")
        if not pinned and not capability and experience.get("route") != "auto":
            raise HubError(
                f"Experience {experience_id} requires model, capability, or route: auto"
            )


def load_catalog(catalog: Path, overlay: Path | None) -> dict[str, Any]:
    merged = read_yaml(catalog)
    if overlay is not None and overlay.exists():
        merged = merge_mapping(merged, read_yaml(overlay, missing_ok=True))
    validate_catalog(merged)
    return merged


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
                raise HubError(f"Invalid or duplicate configured host port: {port}")
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
    result = {key: values[key] for key in PORT_KEYS}
    print(json.dumps({"status": "initialized", "ports": result}, indent=2))
    return 0


def normalize_capabilities(model: dict[str, Any]) -> dict[str, Any]:
    capabilities = model.get("capabilities") or {}
    if isinstance(capabilities, list):
        return {str(item): {} for item in capabilities}
    return dict(capabilities)


def artifact_for(model: dict[str, Any]) -> dict[str, Any]:
    artifact = model.get("artifact") or {"kind": "none"}
    return dict(artifact)


def artifact_filename(model_id: str, model: dict[str, Any]) -> str:
    artifact = artifact_for(model)
    filename = artifact.get("filename")
    if filename:
        return Path(str(filename)).name
    path = artifact.get("path")
    if path and Path(str(path)).suffix.lower() == ".gguf":
        return Path(str(path)).name
    return f"{model_id}.gguf"


def resolved_model_path(model_id: str, model: dict[str, Any], models_dir: Path) -> Path | None:
    artifact = artifact_for(model)
    kind = artifact.get("kind", "none")
    filename = artifact_filename(model_id, model)
    if kind == "managed":
        return models_dir / "managed" / filename
    if kind == "host_path":
        return Path(f"/models/external/{model_id}/{filename}")
    if kind == "docker_volume":
        relative = artifact.get("sub_path") or filename
        return Path(f"/models/external/{model_id}") / str(relative)
    if kind == "container_path":
        return Path(str(artifact["path"]))
    if kind in {"pvc", "hostPath"}:
        relative = artifact.get("sub_path") or filename
        return Path(f"/models/external/{model_id}") / str(relative)
    return None


def ini_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_models_ini(catalog: dict[str, Any], models_dir: Path, fit_target: int) -> str:
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
    for model_id, model in catalog.get("models", {}).items():
        if not model.get("enabled", True):
            continue
        backend = catalog["backends"][model["backend"]]
        if backend.get("kind") != "llama.cpp":
            continue
        path = resolved_model_path(model_id, model, models_dir)
        if path is None:
            continue
        upstream_model = str(model.get("upstream_model") or model_id)
        lines.extend(("", f"[{upstream_model}]", f"model = {path}"))
        runtime = model.get("runtime") or {}
        for key, value in runtime.items():
            if key == "fit-target":
                value = fit_target
            lines.append(f"{key} = {ini_value(value)}")
    return "\n".join(lines) + "\n"


def render_compose_override(catalog: dict[str, Any]) -> dict[str, Any]:
    mounts: list[dict[str, Any]] = []
    external_volumes: dict[str, Any] = {}
    for model_id, model in catalog.get("models", {}).items():
        if not model.get("enabled", True):
            continue
        backend = catalog["backends"][model["backend"]]
        if backend.get("kind") != "llama.cpp":
            continue
        artifact = artifact_for(model)
        kind = artifact.get("kind", "none")
        target_dir = f"/models/external/{model_id}"
        if kind == "host_path":
            source = str(artifact["path"])
            source_path = Path(source)
            if source_path.suffix.lower() == ".gguf":
                target = f"{target_dir}/{artifact_filename(model_id, model)}"
            else:
                target = target_dir
            mounts.append(
                {
                    "type": "bind",
                    "source": source,
                    "target": target,
                    "read_only": bool(artifact.get("read_only", True)),
                }
            )
        elif kind == "docker_volume":
            alias = f"external-{model_id}".replace(".", "-")
            external_volumes[alias] = {"external": True, "name": artifact["volume"]}
            mounts.append(
                {
                    "type": "volume",
                    "source": alias,
                    "target": target_dir,
                    "read_only": bool(artifact.get("read_only", True)),
                }
            )
    result: dict[str, Any] = {"services": {"llama": {}}}
    if mounts:
        result["services"]["llama"]["volumes"] = mounts
    if external_volumes:
        result["volumes"] = external_volumes
    return result


def render_hermes(catalog: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    experiences = catalog.get("experiences", {})
    preferred = "agent" if "agent" in experiences else "chat"
    gateway_key = os.getenv("GATEWAY_API_KEY", "local-only")
    config = {
        "model": {
            "default": preferred,
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
    soul_source = Path("/opt/agent-ui/config/hermes/SOUL.md")
    if not soul_source.exists():
        soul_source = Path("/opt/local-ai-hub/config/hermes/SOUL.md")
    if soul_source.exists():
        atomic_write(directory / "SOUL.md", soul_source.read_text(encoding="utf-8"), mode=0o600)


def resolved_catalog(catalog: dict[str, Any], models_dir: Path) -> dict[str, Any]:
    result = json.loads(json.dumps(catalog))
    result["profiles"] = result.pop("experiences", {})
    for model_id, model in result.get("models", {}).items():
        path = resolved_model_path(model_id, model, models_dir)
        model["capabilities"] = normalize_capabilities(model)
        model["resolved_path"] = str(path) if path else None
        kind = artifact_for(model).get("kind", "none")
        if kind == "managed" and path:
            model["present"] = path.is_file()
            model["size_bytes"] = path.stat().st_size if path.is_file() else None
        elif kind in {"host_path", "docker_volume", "pvc", "hostPath"}:
            model["present"] = None
            model["verification"] = "verified when the generated mount is started"
        else:
            model["present"] = True if kind == "none" else None
    return result


def cmd_runtime_render(args: argparse.Namespace) -> int:
    catalog = load_catalog(
        Path(args.catalog), Path(args.overlay) if args.overlay else None
    )
    models_dir = Path(args.models_dir)
    runtime_dir = Path(args.runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    fit_target = int(os.getenv("LLAMA_FIT_TARGET_MIB", "1024"))
    atomic_write(
        runtime_dir / "models.ini",
        render_models_ini(catalog, models_dir, fit_target),
        mode=0o600,
    )
    resolved = resolved_catalog(catalog, models_dir)
    atomic_write(
        runtime_dir / "catalog.resolved.json",
        json.dumps(resolved, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    atomic_write(
        runtime_dir / "profiles.json",
        json.dumps(resolved, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    atomic_write(runtime_dir / "llama_api_key", os.getenv("LLAMA_API_KEY", "") + "\n")
    if args.compose_output:
        atomic_write(
            Path(args.compose_output),
            yaml.safe_dump(render_compose_override(catalog), sort_keys=False),
            mode=0o600,
        )
    if args.hermes_dir:
        render_hermes(catalog, Path(args.hermes_dir))
    print(
        json.dumps(
            {
                "status": "rendered",
                "backends": len(catalog.get("backends", {})),
                "models": len(catalog.get("models", {})),
                "experiences": len(catalog.get("experiences", {})),
            }
        )
    )
    return 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_for_id(catalog: dict[str, Any], model_id: str) -> dict[str, Any]:
    try:
        return catalog["models"][model_id]
    except KeyError as exc:
        raise HubError(f"Unknown model id: {model_id}") from exc


def managed_path(model_id: str, model: dict[str, Any], models_dir: Path) -> Path:
    if artifact_for(model).get("kind") != "managed":
        raise HubError(f"Model {model_id} is not managed; its existing source is not copied")
    return models_dir / "managed" / artifact_filename(model_id, model)


def copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(6)}.partial")
    try:
        with source.open("rb") as source_handle, temporary.open("wb") as destination_handle:
            shutil.copyfileobj(source_handle, destination_handle, length=8 * 1024 * 1024)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def cmd_model_list(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    data = []
    for model_id, model in catalog.get("models", {}).items():
        path = resolved_model_path(model_id, model, Path(args.models_dir))
        data.append(
            {
                "id": model_id,
                "display_name": model.get("display_name") or model_id,
                "backend": model["backend"],
                "capabilities": sorted(normalize_capabilities(model)),
                "artifact": artifact_for(model),
                "resolved_path": str(path) if path else None,
                "enabled": model.get("enabled", True),
            }
        )
    print(json.dumps({"models": data}, indent=2))
    return 0


def cmd_model_filename(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    print(artifact_filename(args.model_id, model_for_id(catalog, args.model_id)))
    return 0


def cmd_model_import(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    model = model_for_id(catalog, args.model_id)
    source = Path(args.source)
    if not source.is_file() or source.suffix.lower() != ".gguf":
        raise HubError(f"Source must be an existing GGUF file: {source}")
    destination = managed_path(args.model_id, model, Path(args.models_dir))
    if destination.exists() and not args.force:
        raise HubError(f"Destination exists: {destination}; pass --force to replace it")
    copy_atomic(source, destination)
    print(json.dumps({"path": str(destination), "sha256": sha256_file(destination)}))
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
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    model = model_for_id(catalog, args.model_id)
    destination = managed_path(args.model_id, model, Path(args.models_dir))
    source = dict(model.get("source") or {})
    if args.repository:
        source = {"kind": "huggingface", "repository": args.repository, "file": args.file}
    elif args.url:
        source = {"kind": "url", "url": args.url}
    if not source:
        raise HubError("No source configured; supply --repository/--file or --url")
    if destination.exists() and not args.force:
        raise HubError(f"Destination exists: {destination}; pass --force to replace it")
    if source.get("kind") == "huggingface":
        if not source.get("repository") or not source.get("file"):
            raise HubError("Hugging Face sources require repository and file")
        from huggingface_hub import hf_hub_download

        downloaded = Path(
            hf_hub_download(
                repo_id=str(source["repository"]),
                filename=str(source["file"]),
                local_dir=Path(args.models_dir) / ".downloads",
                token=os.getenv("HF_TOKEN") or None,
            )
        )
        copy_atomic(downloaded, destination)
    elif source.get("kind") == "url":
        download_url(str(source["url"]), destination)
    else:
        raise HubError(f"Unsupported source kind: {source.get('kind')}")
    actual = sha256_file(destination)
    expected = source.get("sha256") or model.get("sha256")
    if expected and not secrets.compare_digest(str(expected).lower(), actual.lower()):
        destination.unlink(missing_ok=True)
        raise HubError(f"Checksum mismatch: expected {expected}, got {actual}")
    print(json.dumps({"path": str(destination), "sha256": actual}))
    return 0


def cmd_model_verify(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    model = model_for_id(catalog, args.model_id)
    path = resolved_model_path(args.model_id, model, Path(args.models_dir))
    if path is None:
        print(json.dumps({"id": args.model_id, "verification": "endpoint model"}))
        return 0
    if not path.is_file():
        raise HubError(
            f"Model path is not visible in this container: {path}. For host/volume links, "
            "run ./hub doctor after starting the generated mount."
        )
    actual = sha256_file(path)
    expected = args.sha256 or model.get("sha256")
    if expected and not secrets.compare_digest(str(expected).lower(), actual.lower()):
        raise HubError(f"Checksum mismatch: expected {expected}, got {actual}")
    print(json.dumps({"path": str(path), "size_bytes": path.stat().st_size, "sha256": actual}))
    return 0


def cmd_model_remove(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    model = model_for_id(catalog, args.model_id)
    path = managed_path(args.model_id, model, Path(args.models_dir))
    if not args.yes:
        raise HubError("Refusing to remove managed weights without --yes")
    path.unlink(missing_ok=True)
    print(json.dumps({"removed": str(path)}))
    return 0


def parse_key_values(items: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise HubError(f"Expected KEY=VALUE: {item}")
        key, raw = item.split("=", 1)
        try:
            value: Any = yaml.safe_load(raw)
        except yaml.YAMLError:
            value = raw
        result[key] = value
    return result


def cmd_model_register(args: argparse.Namespace) -> int:
    validate_id(args.model_id, "model")
    overlay_path = Path(args.overlay)
    overlay = read_yaml(overlay_path, missing_ok=True)
    overlay.setdefault("version", 2)
    capabilities = {item: {} for item in args.capability}
    if not capabilities:
        raise HubError("At least one --capability is required")
    artifact: dict[str, Any] = {"kind": args.source_kind}
    if args.path:
        artifact["path"] = args.path
    if args.filename:
        artifact["filename"] = Path(args.filename).name
    if args.volume:
        artifact["volume"] = args.volume
    if args.sub_path:
        artifact["sub_path"] = args.sub_path
    if args.claim_name:
        artifact["claim_name"] = args.claim_name
    runtime = parse_key_values(args.runtime)
    if args.context:
        runtime.setdefault("ctx-size", args.context)
    features: dict[str, Any] = {}
    if args.reasoning:
        features["reasoning"] = {
            "supported": True,
            "request_field": args.reasoning_field,
            "transport": args.reasoning_transport,
            "values": parse_key_values(args.reasoning),
            "unsupported_policy": "reject",
        }
    model: dict[str, Any] = {
        "display_name": args.display_name or args.model_id,
        "description": args.description or "Operator-registered model.",
        "enabled": True,
        "backend": args.backend,
        "upstream_model": args.upstream_model or args.model_id,
        "capabilities": capabilities,
        "features": features,
        "artifact": artifact,
        "runtime": runtime,
        "priority": args.priority,
    }
    overlay.setdefault("models", {})[args.model_id] = model
    if args.experience:
        validate_id(args.experience, "experience")
        overlay.setdefault("experiences", {})[args.experience] = {
            "advertised": True,
            "model": args.model_id,
            "capability": args.capability[0],
            "description": args.description or f"Experience backed by {args.model_id}.",
            "defaults": {},
        }
    base = read_yaml(Path(args.catalog))
    validate_catalog(merge_mapping(base, overlay))
    atomic_write(overlay_path, yaml.safe_dump(overlay, sort_keys=False), mode=0o600)
    print(json.dumps({"registered": args.model_id, "artifact": artifact}, indent=2))
    return 0


def cmd_model_discover(args: argparse.Namespace) -> int:
    root = Path(args.path)
    if not root.exists():
        raise HubError(f"Discovery path does not exist: {root}")
    iterator = root.rglob("*.gguf") if args.recursive else root.glob("*.gguf")
    candidates = []
    for path in sorted(iterator):
        if not path.is_file():
            continue
        model_id = re.sub(r"[^a-z0-9._-]+", "-", path.stem.lower()).strip("-.")[:64]
        candidates.append(
            {
                "id": model_id,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "suggested": {
                    "backend": args.backend,
                    "artifact": {"kind": "host_path", "path": str(path)},
                    "capabilities": {item: {} for item in args.capability},
                },
            }
        )
    print(json.dumps({"candidates": candidates}, indent=2))
    return 0


def cmd_catalog_apply(args: argparse.Namespace) -> int:
    incoming = read_yaml(Path(args.file))
    if incoming.get("version") not in {None, 2}:
        raise HubError("Applied catalog documents must use version: 2")
    incoming.setdefault("version", 2)
    overlay_path = Path(args.overlay)
    current_overlay = {} if args.replace else read_yaml(overlay_path, missing_ok=True)
    candidate_overlay = incoming if args.replace else merge_mapping(current_overlay, incoming)
    candidate_overlay.setdefault("version", 2)
    merged = merge_mapping(read_yaml(Path(args.catalog)), candidate_overlay)
    validate_catalog(merged)
    result = {
        "valid": True,
        "backends": sorted(merged.get("backends", {})),
        "models": sorted(merged.get("models", {})),
        "experiences": sorted(merged.get("experiences", {})),
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        atomic_write(
            overlay_path,
            yaml.safe_dump(candidate_overlay, sort_keys=False),
            mode=0o600,
        )
    print(json.dumps(result, indent=2) if args.json else yaml.safe_dump(result, sort_keys=False))
    return 0


def cmd_catalog_show(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    if args.json:
        print(json.dumps(catalog, indent=2, sort_keys=True))
    else:
        print(yaml.safe_dump(catalog, sort_keys=False), end="")
    return 0


def cmd_catalog_validate(args: argparse.Namespace) -> int:
    candidate = read_yaml(Path(args.file)) if args.file else load_catalog(
        Path(args.catalog), Path(args.overlay) if args.overlay else None
    )
    if args.file:
        if candidate.get("version") != 2:
            candidate = merge_mapping(read_yaml(Path(args.catalog)), candidate)
        validate_catalog(candidate)
    print(json.dumps({"valid": True}))
    return 0


def cmd_catalog_k8s_values(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    values: dict[str, Any] = {
        "models": {},
        "experiences": catalog.get("experiences", {}),
        "hubProfiles": catalog.get("experiences", {}),
        "llama": {"extraVolumes": [], "extraVolumeMounts": []},
    }
    for model_id, model in catalog.get("models", {}).items():
        backend = catalog["backends"][model["backend"]]
        if backend.get("kind") != "llama.cpp":
            continue
        runtime = model.get("runtime") or {}
        path = resolved_model_path(model_id, model, Path("/models"))
        values["models"][model_id] = {
            "enabled": bool(model.get("enabled", True)),
            "containerPath": str(path) if path else "",
            "upstreamModel": model.get("upstream_model") or model_id,
            "contextSize": runtime.get("ctx-size", 8192),
            "gpuLayers": runtime.get("n-gpu-layers", "auto"),
            "cpuMoeLayers": runtime.get("n-cpu-moe", 0),
            "cacheTypeK": runtime.get("cache-type-k", "q8_0"),
            "cacheTypeV": runtime.get("cache-type-v", "q8_0"),
        }
        artifact = artifact_for(model)
        kind = artifact.get("kind")
        volume_name = f"model-{model_id}".replace(".", "-")
        mount_path = f"/models/external/{model_id}"
        if kind == "pvc":
            values["llama"]["extraVolumes"].append(
                {"name": volume_name, "persistentVolumeClaim": {"claimName": artifact["claim_name"]}}
            )
            values["llama"]["extraVolumeMounts"].append(
                {"name": volume_name, "mountPath": mount_path, "readOnly": True}
            )
        elif kind == "hostPath":
            values["llama"]["extraVolumes"].append(
                {"name": volume_name, "hostPath": {"path": artifact["path"], "type": "DirectoryOrCreate"}}
            )
            values["llama"]["extraVolumeMounts"].append(
                {"name": volume_name, "mountPath": mount_path, "readOnly": True}
            )
    output = yaml.safe_dump(values, sort_keys=False)
    if args.output:
        atomic_write(Path(args.output), output, mode=0o600)
    else:
        print(output, end="")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    failures: list[str] = []
    env = parse_env(Path(args.env_file))
    ports: dict[int, str] = {}
    for key in PORT_KEYS:
        try:
            port = int(env.get(key, ""))
        except ValueError:
            failures.append(f"{key} is missing or not an integer")
            continue
        if port in ports:
            failures.append(f"{key} duplicates {ports[port]} on port {port}")
        ports[port] = key
    for key in SECRET_KEYS:
        if len(env.get(key, "")) < 16:
            failures.append(f"{key} is missing or too short")
    try:
        catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    except HubError as exc:
        failures.append(str(exc))
        catalog = {"backends": {}, "models": {}, "experiences": {}}
    report = {
        "valid": not failures,
        "failures": failures,
        "backends": len(catalog.get("backends", {})),
        "models": len(catalog.get("models", {})),
        "experiences": len(catalog.get("experiences", {})),
        "setup_required": not bool(catalog.get("models")),
    }
    print(json.dumps(report, indent=2))
    return 0 if not failures else 1


def add_catalog_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalog", default="/opt/agent-ui/config/models/catalog.yaml")
    parser.add_argument("--overlay", default="/state/catalog.local.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-ui-ctl")
    subparsers = parser.add_subparsers(dest="group", required=True)

    env_parser = subparsers.add_parser("env")
    env_sub = env_parser.add_subparsers(dest="command", required=True)
    env_init = env_sub.add_parser("init")
    env_init.add_argument("--template", default="/workspace/.env.example")
    env_init.add_argument("--output", default="/workspace/.env")
    env_init.add_argument("--reallocate-ports", action="store_true")
    env_init.add_argument("--rotate-secrets", action="store_true")
    env_init.add_argument("--rotate-postgres-password", action="store_true")
    env_init.set_defaults(handler=cmd_env_init)

    runtime_parser = subparsers.add_parser("runtime")
    runtime_sub = runtime_parser.add_subparsers(dest="command", required=True)
    runtime_render = runtime_sub.add_parser("render")
    add_catalog_args(runtime_render)
    runtime_render.add_argument("--models-dir", default="/models")
    runtime_render.add_argument("--runtime-dir", default="/runtime")
    runtime_render.add_argument("--hermes-dir")
    runtime_render.add_argument("--compose-output")
    runtime_render.set_defaults(handler=cmd_runtime_render)

    model_parser = subparsers.add_parser("model")
    model_sub = model_parser.add_subparsers(dest="command", required=True)
    model_list = model_sub.add_parser("list")
    add_catalog_args(model_list)
    model_list.add_argument("--models-dir", default="/models")
    model_list.set_defaults(handler=cmd_model_list)
    model_filename = model_sub.add_parser("filename")
    add_catalog_args(model_filename)
    model_filename.add_argument("model_id")
    model_filename.set_defaults(handler=cmd_model_filename)
    model_import = model_sub.add_parser("import")
    add_catalog_args(model_import)
    model_import.add_argument("model_id")
    model_import.add_argument("--source", required=True)
    model_import.add_argument("--models-dir", default="/models")
    model_import.add_argument("--force", action="store_true")
    model_import.set_defaults(handler=cmd_model_import)
    model_fetch = model_sub.add_parser("fetch")
    add_catalog_args(model_fetch)
    model_fetch.add_argument("model_id")
    model_fetch.add_argument("--models-dir", default="/models")
    model_fetch.add_argument("--repository")
    model_fetch.add_argument("--file")
    model_fetch.add_argument("--url")
    model_fetch.add_argument("--force", action="store_true")
    model_fetch.set_defaults(handler=cmd_model_fetch)
    model_verify = model_sub.add_parser("verify")
    add_catalog_args(model_verify)
    model_verify.add_argument("model_id")
    model_verify.add_argument("--models-dir", default="/models")
    model_verify.add_argument("--sha256")
    model_verify.set_defaults(handler=cmd_model_verify)
    model_remove = model_sub.add_parser("remove")
    add_catalog_args(model_remove)
    model_remove.add_argument("model_id")
    model_remove.add_argument("--models-dir", default="/models")
    model_remove.add_argument("--yes", action="store_true")
    model_remove.set_defaults(handler=cmd_model_remove)
    model_register = model_sub.add_parser("register")
    add_catalog_args(model_register)
    model_register.add_argument("model_id")
    model_register.add_argument("--backend", default="local-llama")
    model_register.add_argument("--upstream-model")
    model_register.add_argument("--display-name")
    model_register.add_argument("--description")
    model_register.add_argument("--capability", action="append", default=[])
    model_register.add_argument("--priority", type=int, default=0)
    model_register.add_argument(
        "--source-kind",
        choices=["managed", "host_path", "docker_volume", "container_path", "pvc", "hostPath", "none"],
        default="none",
    )
    model_register.add_argument("--path")
    model_register.add_argument("--filename")
    model_register.add_argument("--volume")
    model_register.add_argument("--sub-path")
    model_register.add_argument("--claim-name")
    model_register.add_argument("--context", type=int)
    model_register.add_argument("--runtime", action="append", default=[])
    model_register.add_argument("--reasoning", action="append", default=[])
    model_register.add_argument("--reasoning-field", default="reasoning_effort")
    model_register.add_argument(
        "--reasoning-transport", choices=["body", "chat_template_kwargs"], default="body"
    )
    model_register.add_argument("--experience")
    model_register.set_defaults(handler=cmd_model_register)
    model_discover = model_sub.add_parser("discover")
    model_discover.add_argument("path")
    model_discover.add_argument("--recursive", action="store_true")
    model_discover.add_argument("--backend", default="local-llama")
    model_discover.add_argument("--capability", action="append", default=["chat"])
    model_discover.set_defaults(handler=cmd_model_discover)

    catalog_parser = subparsers.add_parser("catalog")
    catalog_sub = catalog_parser.add_subparsers(dest="command", required=True)
    catalog_apply = catalog_sub.add_parser("apply")
    add_catalog_args(catalog_apply)
    catalog_apply.add_argument("--file", required=True)
    catalog_apply.add_argument("--replace", action="store_true")
    catalog_apply.add_argument("--dry-run", action="store_true")
    catalog_apply.add_argument("--json", action="store_true")
    catalog_apply.set_defaults(handler=cmd_catalog_apply)
    catalog_show = catalog_sub.add_parser("show")
    add_catalog_args(catalog_show)
    catalog_show.add_argument("--json", action="store_true")
    catalog_show.set_defaults(handler=cmd_catalog_show)
    catalog_validate = catalog_sub.add_parser("validate")
    add_catalog_args(catalog_validate)
    catalog_validate.add_argument("--file")
    catalog_validate.set_defaults(handler=cmd_catalog_validate)
    k8s_values = catalog_sub.add_parser("k8s-values")
    add_catalog_args(k8s_values)
    k8s_values.add_argument("--output")
    k8s_values.set_defaults(handler=cmd_catalog_k8s_values)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--env-file", default="/workspace/.env")
    add_catalog_args(doctor)
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
