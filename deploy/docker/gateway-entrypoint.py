#!/usr/bin/env python3
"""Locate and launch the repository's ASGI gateway without host-side Python."""

from __future__ import annotations

import ast
import importlib
import os
import sys
from collections.abc import Iterable
from pathlib import Path

import uvicorn


def configured_candidates() -> Iterable[str]:
    configured = os.getenv("GATEWAY_APP", "").strip()
    if configured:
        yield configured
    yield from (
        "local_ai_hub.main:app",
        "local_ai_hub.app:app",
        "local_ai_hub.gateway:app",
        "local_ai_hub.api:app",
        "local_ai_hub.api.main:app",
        "local_ai_hub.server:app",
        "local_ai_gateway.main:app",
        "hub_gateway.main:app",
        "gateway.app:app",
        "gateway.main:app",
        "app.main:app",
        "main:app",
    )


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def source_candidates() -> Iterable[str]:
    """Find an ``app = FastAPI(...)`` assignment in installed source."""
    roots = (Path("/app/src"), Path("/app"))
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if any(part in {"tests", ".venv", "venv", "ops", "deploy"} for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            found = False
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if not isinstance(value, ast.Call) or call_name(value.func) != "FastAPI":
                    continue
                if any(isinstance(target, ast.Name) and target.id == "app" for target in targets):
                    found = True
                    break
            if not found:
                continue
            relative = path.relative_to(root).with_suffix("")
            parts = list(relative.parts)
            if parts[-1] == "__init__":
                parts.pop()
            if parts:
                yield f"{'.'.join(parts)}:app"


def candidates() -> Iterable[str]:
    seen: set[str] = set()
    for reference in (*configured_candidates(), *source_candidates()):
        if reference not in seen:
            seen.add(reference)
            yield reference


def import_app(reference: str):
    module_name, separator, attribute = reference.partition(":")
    if not separator:
        attribute = "app"
    module = importlib.import_module(module_name)
    app = getattr(module, attribute)
    return reference, app


def main() -> int:
    errors: list[str] = []
    for reference in candidates():
        try:
            resolved, app = import_app(reference)
        except (ImportError, AttributeError) as exc:
            errors.append(f"{reference}: {exc}")
            continue

        host = os.getenv("GATEWAY_HOST", "0.0.0.0")
        port = int(os.getenv("GATEWAY_PORT", "8000"))
        workers = int(os.getenv("WORKERS", os.getenv("GATEWAY_WORKERS", "1")))
        log_level = os.getenv("LOG_LEVEL", "info")
        if os.getenv("GATEWAY_CHECK_ONLY", "").lower() in {"1", "true", "yes"}:
            print(f"Resolved ASGI application: {resolved}")
            return 0
        print(f"Launching {resolved} on {host}:{port} with {workers} worker(s)", flush=True)
        target = resolved if workers > 1 else app
        uvicorn.run(target, host=host, port=port, workers=workers, log_level=log_level)
        return 0

    print("Could not locate an ASGI application. Set GATEWAY_APP=module:attribute.", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
