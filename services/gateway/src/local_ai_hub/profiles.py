from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import CapabilitySelector, Endpoint, Model, Profile, Provider, RegistryDocument
from .routing import RouteDecision, choose_automatic_profile


class UnknownModelError(ValueError):
    pass


class UnavailableProfileError(ValueError):
    pass


_ENDPOINT_CAPABILITY = {
    "chat": "chat",
    "completion": "completion",
    "image": "image",
    "embedding": "embedding",
    "rerank": "rerank",
}


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    requested_model: str
    profile_id: str
    profile: Profile
    model_id: str
    model: Model
    provider_id: str
    provider: Provider
    route_reason: str

    @property
    def backend_model(self) -> str:
        return self.model_id

    @property
    def upstream_model(self) -> str:
        return self.model.upstream_model or self.model_id

    @property
    def coordinator_model(self) -> str:
        return self.model.coordinator_model or self.model_id


class ProfileRegistry:
    def __init__(self, document: RegistryDocument):
        self.document = document

    def _model_matches(
        self,
        model: Model,
        endpoint: Endpoint,
        selector: CapabilitySelector,
    ) -> tuple[bool, int]:
        provider = self.document.providers.get(model.provider)
        if not model.enabled or provider is None or not provider.enabled:
            return False, 0
        capabilities = set(model.capabilities)
        tags = set(model.tags)
        endpoint_capability = _ENDPOINT_CAPABILITY[endpoint]
        if endpoint_capability not in capabilities:
            return False, 0
        if not set(selector.all_of).issubset(capabilities):
            return False, 0
        if selector.any_of and not set(selector.any_of).intersection(capabilities):
            return False, 0
        if set(selector.none_of).intersection(capabilities):
            return False, 0
        if not set(selector.tags).issubset(tags):
            return False, 0
        preference = len(set(selector.prefer_capabilities).intersection(capabilities)) * 10
        preference += len(set(selector.prefer_tags).intersection(tags))
        return True, model.priority * 1000 + preference

    def _candidates(
        self,
        endpoint: Endpoint,
        selector: CapabilitySelector,
    ) -> list[tuple[int, str, Model]]:
        candidates: list[tuple[int, str, Model]] = []
        for model_id, model in self.document.models.items():
            matches, score = self._model_matches(model, endpoint, selector)
            if matches:
                candidates.append((score, model_id, model))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates

    def _select_model(self, profile_id: str, profile: Profile) -> tuple[str, Model]:
        if profile.model:
            model = self.document.models.get(profile.model)
            if model is None:
                raise UnavailableProfileError(
                    f"profile {profile_id!r} binds missing model {profile.model!r}"
                )
            matches, _ = self._model_matches(model, profile.endpoint, profile.selector)
            if not matches:
                raise UnavailableProfileError(
                    f"profile {profile_id!r} is incompatible with model {profile.model!r}"
                )
            return profile.model, model

        candidates = self._candidates(profile.endpoint, profile.selector)
        if not candidates and profile.fallback_selector is not None:
            candidates = self._candidates(profile.endpoint, profile.fallback_selector)
        if not candidates:
            required = sorted(
                set(profile.selector.all_of) | {_ENDPOINT_CAPABILITY[profile.endpoint]}
            )
            raise UnavailableProfileError(
                f"profile {profile_id!r} has no enabled model with capabilities {required}"
            )
        _, model_id, model = candidates[0]
        return model_id, model

    def available_profile_ids(self, endpoint: Endpoint = "chat") -> set[str]:
        available: set[str] = set()
        for profile_id, profile in self.document.profiles.items():
            if profile.route == "auto" or profile.endpoint != endpoint:
                continue
            try:
                self._select_model(profile_id, profile)
            except UnavailableProfileError:
                continue
            available.add(profile_id)
        return available

    def advertised_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        for profile_id, profile in self.document.profiles.items():
            if not profile.advertised:
                continue
            if profile.route == "auto":
                if not self.available_profile_ids("chat"):
                    continue
                selected_model = None
                provider_id = None
            else:
                try:
                    selected_model, model = self._select_model(profile_id, profile)
                    provider_id = model.provider
                except UnavailableProfileError:
                    continue
            models.append(
                {
                    "id": profile_id,
                    "object": "model",
                    "owned_by": "agent-ui",
                    "permission": [],
                    "metadata": {
                        "description": profile.description,
                        "route": profile.route,
                        "endpoint": profile.endpoint,
                        "selected_model": selected_model,
                        "provider": provider_id,
                    },
                }
            )
        return models

    def capability_report(self) -> dict[str, Any]:
        profiles: dict[str, Any] = {}
        for profile_id, profile in self.document.profiles.items():
            if profile.route == "auto":
                profiles[profile_id] = {
                    "available": bool(self.available_profile_ids("chat")),
                    "endpoint": profile.endpoint,
                    "route": "auto",
                }
                continue
            try:
                model_id, model = self._select_model(profile_id, profile)
                profiles[profile_id] = {
                    "available": True,
                    "endpoint": profile.endpoint,
                    "model": model_id,
                    "provider": model.provider,
                    "capabilities": model.capabilities,
                }
            except UnavailableProfileError as exc:
                profiles[profile_id] = {
                    "available": False,
                    "endpoint": profile.endpoint,
                    "reason": str(exc),
                }
        return {
            "profiles": profiles,
            "models": {
                model_id: {
                    "enabled": model.enabled,
                    "provider": model.provider,
                    "capabilities": model.capabilities,
                    "tags": model.tags,
                    "features": model.features.model_dump(mode="json"),
                }
                for model_id, model in self.document.models.items()
            },
        }

    def resolve(
        self,
        requested_model: str,
        messages: list[dict[str, Any]],
        explicit_profile: str | None = None,
        endpoint: Endpoint = "chat",
    ) -> ResolvedProfile:
        if explicit_profile:
            selected_id = explicit_profile
            reason = "explicit X-Agent-UI-Profile override"
        elif requested_model in self.document.profiles:
            selected_id = requested_model
            reason = "profile selected explicitly"
        elif requested_model in self.document.models:
            model = self.document.models[requested_model]
            synthetic = Profile(
                endpoint=endpoint,
                model=requested_model,
                advertised=False,
                description=f"Direct access to {requested_model}",
            )
            provider = self.document.providers[model.provider]
            matches, _ = self._model_matches(model, endpoint, synthetic.selector)
            if not matches:
                raise UnavailableProfileError(
                    f"model {requested_model!r} does not support endpoint {endpoint!r}"
                )
            return ResolvedProfile(
                requested_model=requested_model,
                profile_id=requested_model,
                profile=synthetic,
                model_id=requested_model,
                model=model,
                provider_id=model.provider,
                provider=provider,
                route_reason="model selected directly",
            )
        else:
            raise UnknownModelError(f"unknown model/profile: {requested_model}")

        profile = self.document.profiles.get(selected_id)
        if profile is None:
            raise UnknownModelError(f"unknown profile override: {selected_id}")
        if profile.endpoint != endpoint:
            raise UnavailableProfileError(
                f"profile {selected_id!r} serves {profile.endpoint!r}, not {endpoint!r}"
            )
        if profile.route == "auto":
            try:
                decision: RouteDecision = choose_automatic_profile(
                    messages,
                    self.document.routes,
                    self.available_profile_ids("chat"),
                )
            except ValueError as exc:
                raise UnavailableProfileError(str(exc)) from exc
            selected_id = decision.profile_id
            reason = decision.reason
            profile = self.document.profiles[selected_id]

        model_id, model = self._select_model(selected_id, profile)
        provider = self.document.providers[model.provider]
        return ResolvedProfile(
            requested_model=requested_model,
            profile_id=selected_id,
            profile=profile,
            model_id=model_id,
            model=model,
            provider_id=model.provider,
            provider=provider,
            route_reason=reason,
        )
