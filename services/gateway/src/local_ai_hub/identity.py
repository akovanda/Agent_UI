from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Request

from .config import Settings
from .memory_config import IdentityConfig


class IdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PrincipalContext:
    principal_id: str
    source: Literal["forwarded-jwt", "browser-cookie", "api-key", "legacy-loopback"]
    kind: Literal["user", "service"] = "user"
    cookie_authenticated: bool = False


def _decode_segment(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise IdentityError("invalid JWT encoding") from exc


def decode_hs256_jwt(token: str, secret: str) -> dict[str, Any]:
    if not secret:
        raise IdentityError("signed identity is not configured")
    parts = token.split(".")
    if len(parts) != 3:
        raise IdentityError("invalid JWT")
    encoded_header, encoded_payload, encoded_signature = parts
    try:
        header = json.loads(_decode_segment(encoded_header))
        payload = json.loads(_decode_segment(encoded_payload))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IdentityError("invalid JWT payload") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise IdentityError("JWT must use HS256")
    if not isinstance(payload, dict):
        raise IdentityError("invalid JWT claims")
    expected = hmac.new(
        secret.encode(), f"{encoded_header}.{encoded_payload}".encode(), hashlib.sha256
    ).digest()
    supplied = _decode_segment(encoded_signature)
    if not hmac.compare_digest(supplied, expected):
        raise IdentityError("invalid JWT signature")
    now = time.time()
    try:
        expires_at = float(payload["exp"]) if "exp" in payload else None
        not_before = float(payload["nbf"]) if "nbf" in payload else None
    except (TypeError, ValueError) as exc:
        raise IdentityError("invalid JWT time claim") from exc
    if expires_at is not None and expires_at <= now:
        raise IdentityError("JWT has expired")
    if not_before is not None and not_before > now:
        raise IdentityError("JWT is not active")
    return payload


def _principal_claim(claims: dict[str, Any]) -> str:
    for name in ("sub", "id", "user_id"):
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise IdentityError("signed identity has no subject claim")


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def parse_principal_key_map(settings: Settings) -> dict[str, tuple[str, str]]:
    raw = settings.principal_api_keys_json.strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IdentityError("PRINCIPAL_API_KEYS_JSON must be valid JSON") from exc
    if not isinstance(value, dict):
        raise IdentityError("PRINCIPAL_API_KEYS_JSON must be an object")
    result: dict[str, tuple[str, str]] = {}
    for token, binding in value.items():
        if not isinstance(token, str) or not token:
            raise IdentityError("principal API key entries require non-empty tokens")
        if isinstance(binding, str):
            principal, kind = binding, "user"
        elif isinstance(binding, dict):
            principal = binding.get("principal")
            kind = binding.get("kind", "user")
        else:
            raise IdentityError("principal API key bindings must be strings or objects")
        if not isinstance(principal, str) or not principal or kind not in {"user", "service"}:
            raise IdentityError("invalid principal API key binding")
        result[token] = (principal, kind)
    return result


def authenticate_request(
    request: Request,
    settings: Settings,
    identity: IdentityConfig,
    *,
    allow_browser_cookie: bool,
) -> PrincipalContext:
    provided = request.headers.get("Authorization", "")
    bearer = provided.removeprefix("Bearer ") if provided.startswith("Bearer ") else ""
    key_bindings = parse_principal_key_map(settings)

    for token, (principal_id, kind) in key_bindings.items():
        if bearer and hmac.compare_digest(bearer, token):
            return PrincipalContext(principal_id, "api-key", kind=kind)  # type: ignore[arg-type]

    gateway_key = settings.gateway_api_key.get_secret_value()
    gateway_authenticated = bool(bearer) and hmac.compare_digest(bearer, gateway_key)
    forwarded_token = request.headers.get(identity.forwarded_jwt_header)
    if forwarded_token:
        if not gateway_authenticated:
            raise IdentityError("forwarded identity requires the gateway API key")
        secret = os.getenv(identity.forwarded_jwt_secret_env, "")
        return PrincipalContext(
            _principal_claim(decode_hs256_jwt(forwarded_token, secret)), "forwarded-jwt"
        )

    if allow_browser_cookie:
        cookie = request.cookies.get(identity.browser_cookie_name)
        if cookie:
            secret = os.getenv(identity.browser_cookie_secret_env, "")
            return PrincipalContext(
                _principal_claim(decode_hs256_jwt(cookie, secret)),
                "browser-cookie",
                cookie_authenticated=True,
            )

    legacy_user = request.headers.get("X-Agent-UI-User") or request.headers.get("X-Local-AI-User")
    if legacy_user:
        if not gateway_authenticated:
            raise IdentityError("legacy identity requires the gateway API key")
        if not identity.allow_legacy_loopback_headers or not _is_loopback(request):
            raise IdentityError("unsigned user identity headers are disabled")
        return PrincipalContext(legacy_user, "legacy-loopback")

    if gateway_authenticated:
        return PrincipalContext(settings.default_user_id, "api-key")
    raise IdentityError("invalid or missing API key")


def pseudonymous_subject(principal_id: str, secret: str) -> str:
    if not secret:
        raise IdentityError("MEMORY_SUBJECT_HMAC_KEY is not configured")
    return hmac.new(secret.encode(), principal_id.encode(), hashlib.sha256).hexdigest()
