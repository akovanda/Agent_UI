from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class UpstreamStream:
    response: httpx.Response

    async def chunks(self) -> AsyncIterator[bytes]:
        async for chunk in self.response.aiter_raw():
            yield chunk

    async def close(self) -> None:
        await self.response.aclose()


class LlamaUpstream:
    def __init__(self, client: httpx.AsyncClient, api_base_url: str):
        self.client = client
        self.api_base_url = api_base_url.rstrip("/")

    async def chat(self, payload: dict[str, Any]) -> httpx.Response:
        request = self.client.build_request(
            "POST",
            f"{self.api_base_url}/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        return await self.client.send(request, stream=True)
