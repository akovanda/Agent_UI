#!/usr/bin/env python3
"""Containerized control plane for Local AI Hub.

The host contract is deliberately small: Docker, Docker Compose, and a POSIX
shell. Port probing, secret generation, model movement, catalog rendering,
Helm, and kubectl all run inside the toolbox image.
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
import stat
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
MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class HubError(RuntimeError):
    """Expected operator-facing error."""


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def read_yaml(path: Path, *, missing_ok: bool = False) -> dict[str, Any]:
    if not path.exists():
        if missing_ok:
            return {}
        raise HubError(f"Required YAML file does not exist: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
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


def load_catalog(catalog: Path, overlay: Path | None) -> dict[str, Any]:
    merged = read_yaml(catalog)
    if overlay is not None and overlay.exists():
        merged = merge_mapping(merged, read_yaml(overlay, missing_ok=True))
    if merged.get("version") != 1:
        raise HubError("Model catalog must declare version: 1")
    models = merged.get("models")
    profiles = merged.get("profiles")
    if not isinstance(models, dict) or not models:
        raise HubError("Model catalog must contain at least one model")
    if not isinstance(profiles, dict) or not profiles:
        raise HubError("Model catalog must contain at least one profile")
    for model_id, model in models.items():
        validate_model(model_id, model)
    for profile_id, profile in profiles.items():
        if not MODEL_ID_RE.fullmatch(str(profile_id)):
            raise HubError(f"Invalid profile id: {profile_id}")
        if not isinstance(profile, dict):
            raise HubError(f"Profile {profile_id} must be a mapping")
        if profile.get("route") == "auto":
            if profile.get("model") is not None:
                raise HubError(f"Automatic profile {profile_id} cannot select a model")
            continue
        if profile.get("model") not in models:
            raise HubError(
                f"Profile {profile_id} references unknown model {profile.get('model')}"
            )
    return merged


def validate_model(model_id: str, model: Any) -> None:
    if not MODEL_ID_RE.fullmatch(str(model_id)):
        raise HubError(f"Invalid model id: {model_id}")
    if not isinstance(model, dict):
        raise HubError(f"Model {model_id} must be a mapping")
    filename = model.get("filename")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise HubError(f"Model {model_id} has an unsafe filename")
    if not filename.lower().endswith(".gguf"):
        raise HubError(f"Model {model_id} filename must end in .gguf")
    runtime = model.get("runtime", {})
    if not isinstance(runtime, dict):
        raise HubError(f"Model {model_id} runtime must be a mapping")


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
    defaults = parse_env(template)
    values = {**defaults, **existing}

    used: set[int] = set()
    for key in PORT_KEYS:
        current = values.get(key, "")
        if current and not args.reallocate_ports:
            try:
                port = int(current)
            except ValueError as exc:
                raise HubError(f"{key} must be an integer") from exc
            if not 1024 <= port <= 65535:
                raise HubError(f"{key} is outside the valid non-privileged range")
            if port in used:
                raise HubError(f"Duplicate configured host port: {port}")
            used.add(port)
        else:
            values[key] = str(allocate_port(used))

    for key in SECRET_KEYS:
        rotate = args.rotate_secrets
        if key == "POSTGRES_PASSWORD" and output.exists():
            rotate = rotate and getattr(args, "rotate_postgres_password", False)
        if not values.get(key) or rotate:
            values[key] = secrets.token_urlsafe(32)

    atomic_write(output, render_env_template(template, values), mode=0o600)
    print(f"Wrote {output} with generated secrets and unused high host ports.")
    for key in PORT_KEYS:
        print(f"{key}={values[key]}")
    return 0


def resolve_model_file(model: dict[str, Any], models_dir: Path) -> Path:
    candidates = [model["filename"]]
    aliases = model.get("aliases", [])
    if isinstance(aliases, list):
        candidates.extend(str(alias) for alias in aliases)
    for filename in candidates:
        path = models_dir / filename
        if path.is_file():
            return path
    return models_dir / model["filename"]


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
    for model_id, model in catalog["models"].items():
        path = resolve_model_file(model, models_dir)
        lines.extend(("", f"[{model_id}]", f"model = {path}"))
        runtime = model.get("runtime", {})
        for key, value in runtime.items():
            if key == "fit-target":
                value = fit_target
            lines.append(f"{key} = {ini_value(value)}")
    return "\n".join(lines) + "\n"


def render_hermes(catalog: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    hermes_profile = "hermes-agent"
    assistant_model = catalog["profiles"].get(hermes_profile, {}).get(
        "model", "gpt-oss-20b"
    )
    context_length = int(
        catalog["models"].get(assistant_model, {}).get("runtime", {}).get("ctx-size", 65536)
    )
    gateway_key = os.getenv("GATEWAY_API_KEY", "local-only")
    config = {
        "model": {
            "default": hermes_profile,
            "provider": "custom",
            "base_url": "http://gateway:8000/v1",
            "api_key": gateway_key,
            "context_length": context_length,
        },
        "tool_loop_guardrails": {
            "hard_stop_enabled": True,
            "hard_stop_after": {
                "exact_failure": 5,
                "idempotent_no_progress": 5,
            },
        },
        "database": {"journal_mode": "wal"},
    }
    atomic_write(directory / "config.yaml", yaml.safe_dump(config, sort_keys=False), mode=0o600)
    soul_source = Path("/opt/local-ai-hub/config/hermes/SOUL.md")
    if soul_source.exists():
        atomic_write(directory / "SOUL.md", soul_source.read_text(encoding="utf-8"), mode=0o600)


def cmd_runtime_render(args: argparse.Namespace) -> int:
    catalog_path = Path(args.catalog)
    overlay_path = Path(args.overlay) if args.overlay else None
    models_dir = Path(args.models_dir)
    runtime_dir = Path(args.runtime_dir)
    catalog = load_catalog(catalog_path, overlay_path)
    fit_target = int(os.getenv("LLAMA_FIT_TARGET_MIB", "1024"))

    runtime_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(
        runtime_dir / "models.ini",
        render_models_ini(catalog, models_dir, fit_target),
        mode=0o600,
    )

    resolved: dict[str, Any] = {"version": 1, "models": {}, "profiles": catalog["profiles"]}
    missing: list[str] = []
    for model_id, model in catalog["models"].items():
        path = resolve_model_file(model, models_dir)
        item = dict(model)
        item["resolved_path"] = str(path)
        item["present"] = path.is_file()
        item["size_bytes"] = path.stat().st_size if path.is_file() else None
        resolved["models"][model_id] = item
        if not path.is_file():
            missing.append(model_id)
    atomic_write(
        runtime_dir / "catalog.resolved.json",
        json.dumps(resolved, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    atomic_write(
        runtime_dir / "profiles.json",
        json.dumps(catalog["profiles"], indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )
    atomic_write(
        runtime_dir / "llama_api_key",
        os.getenv("LLAMA_API_KEY", "") + "\n",
        mode=0o644,
    )
    if args.hermes_dir:
        render_hermes(catalog, Path(args.hermes_dir))

    print(f"Rendered {runtime_dir / 'models.ini'}")
    if missing:
        print("Models not yet imported: " + ", ".join(missing))
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


def cmd_model_list(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    models_dir = Path(args.models_dir)
    print(f"{'ID':24} {'PRESENT':8} {'SIZE_GIB':>9}  FILE")
    for model_id, model in catalog["models"].items():
        path = resolve_model_file(model, models_dir)
        present = path.is_file()
        size = f"{path.stat().st_size / (1024 ** 3):.2f}" if present else "-"
        print(f"{model_id:24} {str(present).lower():8} {size:>9}  {path.name}")
    return 0


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



def cmd_model_filename(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    model = model_for_id(catalog, args.model_id)
    print(model["filename"])
    return 0


def cmd_model_import(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    model = model_for_id(catalog, args.model_id)
    source = Path(args.source)
    if not source.is_file():
        raise HubError(f"Source GGUF does not exist: {source}")
    if source.suffix.lower() != ".gguf":
        raise HubError("Only GGUF files can be imported")
    destination = Path(args.models_dir) / model["filename"]
    if destination.exists() and not args.force:
        raise HubError(f"Destination already exists: {destination}; pass --force to replace it")
    copy_atomic(source, destination)
    digest = sha256_file(destination)
    print(f"Imported {source.name} as {destination.name}")
    print(f"sha256={digest}")
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
    source = dict(model.get("source") or {})
    if args.repository:
        source = {"kind": "huggingface", "repository": args.repository, "file": args.file}
    elif args.url:
        source = {"kind": "url", "url": args.url}
    if not source:
        raise HubError(
            "No catalog source is defined. Supply --repository and --file, or supply --url."
        )

    destination = Path(args.models_dir) / model["filename"]
    if destination.exists() and not args.force:
        raise HubError(f"Destination already exists: {destination}; pass --force to replace it")
    kind = source.get("kind")
    if kind == "huggingface":
        repository = source.get("repository")
        filename = source.get("file")
        if not repository or not filename:
            raise HubError("Hugging Face sources require repository and file")
        from huggingface_hub import hf_hub_download

        downloaded = Path(
            hf_hub_download(
                repo_id=str(repository),
                filename=str(filename),
                local_dir=Path(args.models_dir) / ".downloads",
                token=os.getenv("HF_TOKEN") or None,
            )
        )
        copy_atomic(downloaded, destination)
    elif kind == "url":
        url = source.get("url")
        if not url:
            raise HubError("URL source is missing url")
        download_url(str(url), destination)
    else:
        raise HubError(f"Unsupported source kind: {kind}")

    expected = source.get("sha256") or model.get("sha256")
    actual = sha256_file(destination)
    if expected and not secrets.compare_digest(str(expected).lower(), actual.lower()):
        destination.unlink(missing_ok=True)
        raise HubError(f"Checksum mismatch: expected {expected}, got {actual}")
    print(f"Fetched {args.model_id} to {destination}")
    print(f"sha256={actual}")
    return 0


def cmd_model_verify(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    model = model_for_id(catalog, args.model_id)
    path = resolve_model_file(model, Path(args.models_dir))
    if not path.is_file():
        raise HubError(f"Model is not present: {args.model_id}")
    actual = sha256_file(path)
    expected = args.sha256 or model.get("sha256") or (model.get("source") or {}).get("sha256")
    print(f"path={path}")
    print(f"size_bytes={path.stat().st_size}")
    print(f"sha256={actual}")
    if expected and not secrets.compare_digest(str(expected).lower(), actual.lower()):
        raise HubError(f"Checksum mismatch: expected {expected}")
    return 0


def cmd_model_remove(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    model = model_for_id(catalog, args.model_id)
    path = resolve_model_file(model, Path(args.models_dir))
    if not path.exists():
        print(f"Model is already absent: {args.model_id}")
        return 0
    if not args.yes:
        raise HubError("Refusing to remove a model without --yes")
    path.unlink()
    print(f"Removed {path}")
    return 0


def cmd_model_register(args: argparse.Namespace) -> int:
    if not MODEL_ID_RE.fullmatch(args.model_id):
        raise HubError(f"Invalid model id: {args.model_id}")
    filename = Path(args.filename).name
    if filename != args.filename or not filename.lower().endswith(".gguf"):
        raise HubError("--filename must be a plain GGUF filename")
    overlay_path = Path(args.overlay)
    overlay = read_yaml(overlay_path, missing_ok=True)
    overlay.setdefault("version", 1)
    models = overlay.setdefault("models", {})
    runtime = {
        "ctx-size": args.context,
        "n-gpu-layers": args.gpu_layers,
        "cache-type-k": args.cache_type,
        "cache-type-v": args.cache_type,
        "flash-attn": "auto",
        "fit": True,
        "fit-target": int(os.getenv("LLAMA_FIT_TARGET_MIB", "1024")),
        "parallel": 1,
        "jinja": True,
        "load-on-startup": False,
        "stop-timeout": 180,
    }
    model: dict[str, Any] = {
        "display_name": args.display_name or args.model_id,
        "role": args.role,
        "filename": filename,
        "aliases": [],
        "description": args.description or "Locally registered model.",
        "source": {},
        "runtime": runtime,
    }
    if args.repository and args.file:
        model["source"] = {
            "kind": "huggingface",
            "repository": args.repository,
            "file": args.file,
        }
    elif args.url:
        model["source"] = {"kind": "url", "url": args.url}
    models[args.model_id] = model
    atomic_write(overlay_path, yaml.safe_dump(overlay, sort_keys=False), mode=0o600)
    print(f"Registered {args.model_id} in {overlay_path}")
    return 0


def cmd_catalog_k8s_values(args: argparse.Namespace) -> int:
    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)
    values: dict[str, Any] = {"models": {}, "hubProfiles": catalog["profiles"]}
    for model_id, model in catalog["models"].items():
        runtime = model.get("runtime", {})
        values["models"][model_id] = {
            "filename": model["filename"],
            "contextSize": runtime.get("ctx-size", 8192),
            "gpuLayers": runtime.get("n-gpu-layers", "auto"),
            "cpuMoeLayers": runtime.get("n-cpu-moe", 0),
            "cacheTypeK": runtime.get("cache-type-k", "q8_0"),
            "cacheTypeV": runtime.get("cache-type-v", "q8_0"),
            "enabled": True,
        }
    output = yaml.safe_dump(values, sort_keys=False)
    if args.output:
        atomic_write(Path(args.output), output, mode=0o600)
        print(f"Wrote {args.output}")
    else:
        print(output, end="")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    failures: list[str] = []
    env = parse_env(Path(args.env_file))
    ports: dict[int, str] = {}
    for key in PORT_KEYS:
        raw = env.get(key, "")
        try:
            port = int(raw)
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
        catalog = None
    if catalog is not None:
        print(f"catalog_models={len(catalog['models'])}")
        print(f"catalog_profiles={len(catalog['profiles'])}")
    if failures:
        for failure in failures:
            eprint(f"FAIL: {failure}")
        return 1
    print("Environment and catalog validation passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hubctl")
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
    model_register.add_argument("model_id")
    model_register.add_argument("--overlay", default="/state/catalog.local.yaml")
    model_register.add_argument("--filename", required=True)
    model_register.add_argument("--display-name")
    model_register.add_argument("--description")
    model_register.add_argument("--role", default="general")
    model_register.add_argument("--context", type=int, default=32768)
    model_register.add_argument("--gpu-layers", default="auto")
    model_register.add_argument("--cache-type", default="q8_0")
    model_register.add_argument("--repository")
    model_register.add_argument("--file")
    model_register.add_argument("--url")
    model_register.set_defaults(handler=cmd_model_register)

    catalog_parser = subparsers.add_parser("catalog")
    catalog_sub = catalog_parser.add_subparsers(dest="command", required=True)
    k8s_values = catalog_sub.add_parser("k8s-values")
    add_catalog_args(k8s_values)
    k8s_values.add_argument("--output")
    k8s_values.set_defaults(handler=cmd_catalog_k8s_values)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--env-file", default="/workspace/.env")
    add_catalog_args(doctor)
    doctor.set_defaults(handler=cmd_doctor)
    return parser


def add_catalog_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--catalog",
        default="/opt/local-ai-hub/config/models/catalog.yaml",
    )
    parser.add_argument("--overlay", default="/state/catalog.local.yaml")


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
