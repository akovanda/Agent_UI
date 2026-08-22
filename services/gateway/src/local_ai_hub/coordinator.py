from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from .observability import MODEL_LOAD_SECONDS

logger = logging.getLogger(__name__)

ReadyState = Literal["loaded", "sleeping"]


class ModelCoordinationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelStatus:
    model_id: str
    value: str
    failed: bool = False
    exit_code: int | None = None


class LlamaModelCoordinator:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        mode: Literal["explicit", "autoload", "none"],
        timeout_seconds: float,
        poll_interval_seconds: float,
        transition_lock: asyncio.Lock,
    ):
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.transition_lock = transition_lock

    async def list_models(self, reload: bool = False) -> list[ModelStatus]:
        params = {"reload": "1"} if reload else None
        try:
            response = await self.client.get(f"{self.base_url}/models", params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data", []), list):
                raise ValueError("response must contain a data array")
            records = payload.get("data", [])
            statuses: list[ModelStatus] = []
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("model records must be objects")
                status = record.get("status") or {}
                if not isinstance(status, dict):
                    raise ValueError("model status must be an object")
                model_id = record.get("id")
                if not isinstance(model_id, str) or not model_id:
                    raise ValueError("model record is missing a string id")
                statuses.append(
                    ModelStatus(
                        model_id=model_id,
                        value=str(status.get("value", "unknown")),
                        failed=bool(status.get("failed", False)),
                        exit_code=status.get("exit_code"),
                    )
                )
            return statuses
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelCoordinationError(f"failed to list llama.cpp models: {exc}") from exc

    async def ensure_loaded(self, target_model: str) -> None:
        if self.mode in {"none", "autoload"}:
            return

        started = time.monotonic()
        result = "error"
        try:
            async with self.transition_lock:
                statuses = await self.list_models()
                by_id = {status.model_id: status for status in statuses}
                if target_model not in by_id:
                    statuses = await self.list_models(reload=True)
                    by_id = {status.model_id: status for status in statuses}
                if target_model not in by_id:
                    raise ModelCoordinationError(
                        f"llama.cpp router does not advertise model {target_model!r}"
                    )

                target = by_id[target_model]
                if target.value in {"loaded", "sleeping"} and not target.failed:
                    result = "already_ready"
                    return
                if target.value == "loading" and not target.failed:
                    await self._wait_for(target_model, {"loaded", "sleeping"})
                    result = "already_loading"
                    return

                for status in statuses:
                    if status.model_id == target_model:
                        continue
                    if status.value in {"loaded", "loading", "sleeping"}:
                        logger.info(
                            "unloading model %s before loading %s",
                            status.model_id,
                            target_model,
                        )
                        response = await self.client.post(
                            f"{self.base_url}/models/unload", json={"model": status.model_id}
                        )
                        response.raise_for_status()
                        await self._wait_for(status.model_id, {"unloaded"})

                logger.info("loading model %s", target_model)
                response = await self.client.post(
                    f"{self.base_url}/models/load", json={"model": target_model}
                )
                response.raise_for_status()
                await self._wait_for(target_model, {"loaded", "sleeping"})
                result = "loaded"
        except (httpx.HTTPError, ValueError) as exc:
            raise ModelCoordinationError(
                f"model transition failed for {target_model}: {exc}"
            ) from exc
        finally:
            MODEL_LOAD_SECONDS.labels(model=target_model, result=result).observe(
                time.monotonic() - started
            )

    async def _wait_for(self, model_id: str, expected: set[str]) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        last_status: ModelStatus | None = None
        while time.monotonic() < deadline:
            statuses = await self.list_models()
            last_status = next((item for item in statuses if item.model_id == model_id), None)
            if last_status and last_status.failed:
                raise ModelCoordinationError(
                    f"model {model_id} failed with exit code {last_status.exit_code}"
                )
            if last_status and last_status.value in expected:
                return
            await asyncio.sleep(self.poll_interval_seconds)
        value = last_status.value if last_status else "missing"
        raise ModelCoordinationError(
            f"timed out waiting for {model_id} to reach {sorted(expected)}; last status={value}"
        )

    async def status_payload(self) -> dict[str, Any]:
        statuses = await self.list_models()
        return {
            "mode": self.mode,
            "models": [
                {
                    "id": status.model_id,
                    "status": status.value,
                    "failed": status.failed,
                    "exit_code": status.exit_code,
                }
                for status in statuses
            ],
        }
