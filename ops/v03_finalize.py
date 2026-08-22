#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if text.count(old) != 1:
        raise RuntimeError(f"Expected one match in {path}, found {text.count(old)}")
    write(path, text.replace(old, new, 1))


def patch_config() -> None:
    path = "services/gateway/src/local_ai_hub/config.py"
    replace_once(
        path,
        '''        if unknown_models:\n            raise ValueError(f"experiences reference unknown models: {sorted(unknown_models)}")\n        return self\n''',
        '''        if unknown_models:\n            raise ValueError(f"experiences reference unknown models: {sorted(unknown_models)}")\n        incompatible = []\n        for profile_id, profile in self.profiles.items():\n            if not profile.backend_model or not profile.capability:\n                continue\n            model = self.models.get(profile.backend_model)\n            if model is not None and not model.has_capability(profile.capability):\n                incompatible.append(\n                    f"{profile_id} requires {profile.capability} from {profile.backend_model}"\n                )\n        if incompatible:\n            raise ValueError(f"incompatible pinned experiences: {sorted(incompatible)}")\n        return self\n''',
    )
    text = read(path)
    marker = "\ndef _expand_legacy(raw: Any) -> dict[str, Any]:\n"
    helper = '''\ndef _legacy_model_spec(name: str, metadata: Any) -> dict[str, Any]:\n    details = metadata if isinstance(metadata, dict) else {}\n    capabilities = details.get("capabilities") or {"chat": {}}\n    if isinstance(capabilities, list):\n        capabilities = {str(item): {} for item in capabilities}\n    features = dict(details.get("features") or {})\n    model_metadata = dict(details.get("metadata") or {})\n    model_metadata.setdefault("advertise_direct", False)\n    family = str(details.get("family", "")).lower()\n    if family == "gpt-oss":\n        features.setdefault(\n            "reasoning",\n            {\n                "supported": True,\n                "request_field": "reasoning_effort",\n                "transport": "body",\n                "values": {"none": None, "low": "low", "medium": "medium", "high": "high"},\n                "unsupported_policy": "reject",\n            },\n        )\n        model_metadata.setdefault("instruction_role", "developer")\n    return {\n        "backend": "local-llama",\n        "upstream_model": name,\n        "description": details.get("description", ""),\n        "capabilities": capabilities,\n        "features": features,\n        "metadata": model_metadata,\n    }\n\n\ndef _expand_legacy(raw: Any) -> dict[str, Any]:\n'''
    if marker not in text:
        raise RuntimeError("legacy expansion marker missing")
    text = text.replace(marker, helper, 1)
    old = '''        models = {\n            name: {\n                "backend": "local-llama",\n                "upstream_model": name,\n                "description": value.get("description", ""),\n                "capabilities": {"chat": {}},\n            }\n            for name, value in raw.get("backends", {}).items()\n        }\n'''
    new = '''        models = {\n            name: _legacy_model_spec(name, value)\n            for name, value in raw.get("backends", {}).items()\n        }\n'''
    if old not in text:
        raise RuntimeError("legacy full-document model block missing")
    text = text.replace(old, new, 1)
    old_shorthand = '''            models.setdefault(\n                backend_model,\n                {\n                    "backend": "local-llama",\n                    "upstream_model": backend_model,\n                    "description": metadata.get("description", "")\n                    if isinstance(metadata, dict)\n                    else "",\n                    "capabilities": capabilities or {"chat": {}},\n                },\n            )\n'''
    new_shorthand = '''            legacy_spec = _legacy_model_spec(backend_model, metadata)\n            if capabilities:\n                legacy_spec["capabilities"] = capabilities\n            models.setdefault(backend_model, legacy_spec)\n'''
    if old_shorthand not in text:
        raise RuntimeError("legacy shorthand model block missing")
    text = text.replace(old_shorthand, new_shorthand, 1)
    text = text.replace(
        '''                "serialize_requests": True,\n            },\n''',
        '''                "serialize_requests": True,\n                "options": {"legacy": True},\n            },\n''',
        2,
    )
    write(path, text)


def patch_profiles() -> None:
    replace_once(
        "services/gateway/src/local_ai_hub/profiles.py",
        '''        for model_id, model in self.document.models.items():\n            if not model.enabled:\n                continue\n            backend = self.document.backends.get(model.backend)\n''',
        '''        for model_id, model in self.document.models.items():\n            if not model.enabled or model.metadata.get("advertise_direct", True) is False:\n                continue\n            backend = self.document.backends.get(model.backend)\n''',
    )


def patch_backends() -> None:
    path = "services/gateway/src/local_ai_hub/backends.py"
    replace_once(
        path,
        '''    "chat": "/v1/chat/completions",\n    "completions": "/v1/completions",\n''',
        '''    "chat": "/v1/chat/completions",\n    "responses": "/v1/responses",\n    "completions": "/v1/completions",\n''',
    )
    replace_once(
        path,
        '''            base_url = spec.resolved_base_url\n            if not base_url:\n''',
        '''            base_url = spec.resolved_base_url\n            if spec.options.get("legacy") and spec.kind == "llama.cpp":\n                base_url = settings.llama_base_url\n                spec.coordinator = settings.model_coordinator_mode\n            if not base_url:\n''',
    )
    replace_once(
        path,
        '''                    poll_interval_seconds=settings.model_poll_interval_seconds,\n                    transition_lock=gate.transition_lock if gate else None,\n                )\n''',
        '''                    poll_interval_seconds=settings.model_poll_interval_seconds,\n                    transition_lock=gate.transition_lock if gate else None,\n                    fixed_backend_model=settings.fixed_backend_model,\n                )\n''',
    )


def patch_app_compatibility() -> None:
    path = "services/gateway/src/local_ai_hub/app.py"
    replace_once(
        path,
        '''            "experience": resolved.profile_id,\n            "model": resolved.backend_model,\n            "backend": resolved.backend_id,\n''',
        '''            "experience": resolved.profile_id,\n            "profile": resolved.profile_id,\n            "model": resolved.backend_model,\n            "backend_model": resolved.backend_model,\n            "backend": resolved.backend_id,\n''',
    )
    replace_once(
        path,
        '''            "experiences": sorted(new_document.profiles),\n        }\n''',
        '''            "experiences": sorted(new_document.profiles),\n            "profiles": sorted(new_document.profiles),\n        }\n''',
    )
    replace_once(
        path,
        '''    @application.get("/api/models/status")\n    async def model_status(request: Request) -> dict[str, Any]:\n        runtime: Runtime = request.app.state.runtime\n        return await runtime.backends.status()\n''',
        '''    @application.get("/api/models/status")\n    async def model_status(request: Request) -> dict[str, Any]:\n        runtime: Runtime = request.app.state.runtime\n        coordinated = [\n            item for item in runtime.backends.runtimes.values() if item.coordinator is not None\n        ]\n        if len(coordinated) == 1:\n            status = await coordinated[0].coordinator.status()\n            status["backend_id"] = coordinated[0].backend_id\n            return status\n        return {"backends": await runtime.backends.status()}\n''',
    )


def patch_hubctl() -> None:
    path = "ops/hubctl.py"
    replace_once(
        path,
        '''        if kind == "hostPath" and not artifact.get("path"):\n            raise HubError(f"Model {model_id} hostPath artifact requires path")\n\n    for experience_id, experience in catalog.get("experiences", {}).items():\n''',
        '''        if kind == "hostPath" and not artifact.get("path"):\n            raise HubError(f"Model {model_id} hostPath artifact requires path")\n        backend = backends[backend_id]\n        if backend.get("kind") == "llama.cpp" and kind == "none":\n            raise HubError(\n                f"Local llama.cpp model {model_id} requires a local artifact; "\n                "use an openai-compatible backend for an existing inference API"\n            )\n\n    for experience_id, experience in catalog.get("experiences", {}).items():\n''',
    )
    text = read(path)
    pattern = re.compile(
        r"def cmd_catalog_k8s_values\(args: argparse\.Namespace\) -> int:\n.*?\n\ndef cmd_doctor",
        re.DOTALL,
    )
    replacement = '''def catalog_to_k8s_values(catalog: dict[str, Any]) -> dict[str, Any]:\n    values: dict[str, Any] = {\n        "models": {},\n        "experiences": catalog.get("experiences", {}),\n        "hubProfiles": catalog.get("experiences", {}),\n        "llama": {"extraVolumes": [], "extraVolumeMounts": []},\n    }\n    for model_id, model in catalog.get("models", {}).items():\n        backend = catalog["backends"][model["backend"]]\n        if backend.get("kind") != "llama.cpp":\n            continue\n        runtime = model.get("runtime") or {}\n        path = resolved_model_path(model_id, model, Path("/models"))\n        values["models"][model_id] = {\n            "enabled": bool(model.get("enabled", True)),\n            "displayName": model.get("display_name") or model_id,\n            "backend": model.get("backend", "local-llama"),\n            "upstreamModel": model.get("upstream_model") or model_id,\n            "priority": int(model.get("priority", 0)),\n            "capabilities": normalize_capabilities(model),\n            "features": model.get("features") or {},\n            "containerPath": str(path) if path else "",\n            "contextSize": runtime.get("ctx-size", 8192),\n            "gpuLayers": runtime.get("n-gpu-layers", "auto"),\n            "cpuMoeLayers": runtime.get("n-cpu-moe", 0),\n            "cacheTypeK": runtime.get("cache-type-k", "q8_0"),\n            "cacheTypeV": runtime.get("cache-type-v", "q8_0"),\n        }\n        artifact = artifact_for(model)\n        kind = artifact.get("kind")\n        volume_name = f"model-{model_id}".replace(".", "-")\n        mount_path = f"/models/external/{model_id}"\n        if kind == "pvc":\n            values["llama"]["extraVolumes"].append(\n                {\n                    "name": volume_name,\n                    "persistentVolumeClaim": {"claimName": artifact["claim_name"]},\n                }\n            )\n            values["llama"]["extraVolumeMounts"].append(\n                {"name": volume_name, "mountPath": mount_path, "readOnly": True}\n            )\n        elif kind == "hostPath":\n            values["llama"]["extraVolumes"].append(\n                {\n                    "name": volume_name,\n                    "hostPath": {"path": artifact["path"], "type": "Directory"},\n                }\n            )\n            values["llama"]["extraVolumeMounts"].append(\n                {"name": volume_name, "mountPath": mount_path, "readOnly": True}\n            )\n    return values\n\n\ndef cmd_catalog_k8s_values(args: argparse.Namespace) -> int:\n    catalog = load_catalog(Path(args.catalog), Path(args.overlay) if args.overlay else None)\n    output = yaml.safe_dump(catalog_to_k8s_values(catalog), sort_keys=False)\n    if args.output:\n        atomic_write(Path(args.output), output, mode=0o600)\n    else:\n        print(output, end="")\n    return 0\n\n\ndef cmd_doctor'''
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError("k8s values function block missing")
    write(path, text)


def patch_ruff() -> None:
    replace_once(
        "pyproject.toml",
        'ignore = ["B008"]\n',
        'ignore = ["B008", "E501"]\n',
    )


def clean_one_shot_files() -> None:
    for relative in (
        "ops/v03_finalize.py",
        ".github/workflows/v03-finalize.yml",
        ".v03-finalize-trigger",
    ):
        (ROOT / relative).unlink(missing_ok=True)


def main() -> None:
    patch_config()
    patch_profiles()
    patch_backends()
    patch_app_compatibility()
    patch_hubctl()
    patch_ruff()
    clean_one_shot_files()


if __name__ == "__main__":
    main()
