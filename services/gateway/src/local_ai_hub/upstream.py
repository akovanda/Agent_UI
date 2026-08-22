from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .config import Provider

_ENV_VALUE = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")
_DEFAULT_ENDPOINTS = {
    "chat": "chat/completions",
    "completion": "completions",
    "image": "images/generations",
    "embedding": "embeddings",
    "rerank": "rerank",
}


class ProviderConfigurationError(RuntimeError):
    pass


class ProviderUpstream:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    def _headers(self, provider: Provider) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if provider.api_key_env:
            token = os.getenv(provider.api_key_env)
            if not token:
                raise ProviderConfigurationError(
                    f"provider API key environment variable is missing: {provider.api_key_env}"
                )
            headers["Authorization"] = f"Bearer {token}"
        for key, value in provider.headers.items():
            match = _ENV_VALUE.fullmatch(value)
            if match:
                env_value = os.getenv(match.group(1))
                if env_value is None:
                    raise ProviderConfigurationError(
                        f"provider header environment variable is missing: {match.group(1)}"
                    )
                headers[key] = env_value
            else:
                headers[key] = value
        return headers

    async def request(
        self,
        provider: Provider,
        endpoint: str,
        payload: dict[str, Any],
        *,
        stream: bool = False,
    ) -> httpx.Response:
        path = provider.endpoints.get(endpoint, _DEFAULT_ENDPOINTS[endpoint])
        url = f"{provider.base_url.rstrip('/')}/{path.lstrip('/')}"
        request = self.client.build_request(
            "POST",
            url,
            json=payload,
            headers=self._headers(provider),
        )
        return await self.client.send(request, stream=stream)


# Compatibility name for downstream code written against the 0.2 gateway.
LlamaUpstream = ProviderUpstream
