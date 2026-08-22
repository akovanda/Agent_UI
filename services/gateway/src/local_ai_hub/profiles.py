from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Backend, ModelSpec, Profile, ProfileDocument
from .routing import RouteDecision, choose_automatic_profile


class UnknownModelError(ValueError):
    pass


class UnavailableExperienceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    requested_model: str
    profile_id: str
    profile: Profile
    backend_model: str
    route_reason: str
    model: ModelSpec
    backend_id: str
    backend: Backend


class ProfileRegistry:
    """Resolve stable human-facing experiences to operator-registered models."""

    def __init__(self, document: ProfileDocument):
        self.document = document

    def _candidates(self, capability: str | None) -> list[tuple[str, ModelSpec]]:
        candidates = [
            (model_id, model)
            for model_id, model in self.document.models.items()
            if model.enabled
            and self.document.backends.get(model.backend)
            and self.document.backends[model.backend].enabled
            and (not capability or model.has_capability(capability))
        ]
        return sorted(candidates, key=lambda item: (-item[1].priority, item[0]))

    def experience_available(self, profile: Profile) -> bool:
        if profile.route == "auto":
            return bool(self._candidates(None))
        if profile.backend_model:
            model = self.document.models.get(profile.backend_model)
            return bool(
                model
                and model.enabled
                and model.backend in self.document.backends
                and self.document.backends[model.backend].enabled
                and (not profile.capability or model.has_capability(profile.capability))
            )
        return bool(self._candidates(profile.capability))

    def advertised_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        for profile_id, profile in self.document.profiles.items():
            if not profile.advertised:
                continue
            models.append(
                {
                    "id": profile_id,
                    "object": "model",
                    "owned_by": "agent-ui",
                    "permission": [],
                    "metadata": {
                        "type": "experience",
                        "description": profile.description,
                        "capability": profile.capability,
                        "route": profile.route,
                        "pinned_model": profile.backend_model,
                        "available": self.experience_available(profile),
                    },
                }
            )
        for model_id, model in self.document.models.items():
            if not model.enabled:
                continue
            backend = self.document.backends.get(model.backend)
            models.append(
                {
                    "id": model_id,
                    "object": "model",
                    "owned_by": "agent-ui",
                    "permission": [],
                    "metadata": {
                        "type": "registered-model",
                        "display_name": model.display_name or model_id,
                        "description": model.description,
                        "backend": model.backend,
                        "backend_kind": backend.kind if backend else None,
                        "capabilities": sorted(model.capabilities),
                        "reasoning": (
                            model.features.reasoning.model_dump()
                            if model.features.reasoning
                            else None
                        ),
                        "priority": model.priority,
                        "available": bool(backend and backend.enabled),
                    },
                }
            )
        return models

    def _resolve_auto(self, messages: list[dict[str, Any]]) -> tuple[str, str]:
        decision: RouteDecision = choose_automatic_profile(messages)
        if decision.profile_id in self.document.profiles:
            return decision.profile_id, decision.reason
        for preferred in ("chat", "code", "story", "agent"):
            profile = self.document.profiles.get(preferred)
            if profile and self.experience_available(profile):
                return preferred, "automatic fallback selected an available experience"
        for profile_id, profile in self.document.profiles.items():
            if profile.route != "auto" and self.experience_available(profile):
                return profile_id, "automatic fallback selected the first available experience"
        raise UnavailableExperienceError("no registered model can satisfy an automatic route")

    def _select_model(self, profile_id: str, profile: Profile) -> tuple[str, str]:
        if profile.backend_model:
            model = self.document.models.get(profile.backend_model)
            if model is None or not model.enabled:
                raise UnavailableExperienceError(
                    f"experience {profile_id!r} is pinned to unavailable model "
                    f"{profile.backend_model!r}"
                )
            if profile.capability and not model.has_capability(profile.capability):
                raise UnavailableExperienceError(
                    f"model {profile.backend_model!r} does not declare capability "
                    f"{profile.capability!r}"
                )
            return profile.backend_model, "experience selected its pinned model"
        candidates = self._candidates(profile.capability)
        if not candidates:
            raise UnavailableExperienceError(
                f"experience {profile_id!r} requires capability {profile.capability!r}; "
                "register or enable a matching model"
            )
        return candidates[0][0], "selected highest-priority capable model"

    def resolve(
        self,
        requested_model: str,
        messages: list[dict[str, Any]],
        explicit_profile: str | None = None,
        required_capability: str | None = None,
    ) -> ResolvedProfile:
        if explicit_profile:
            if explicit_profile not in self.document.profiles:
                raise UnknownModelError(f"unknown experience override: {explicit_profile}")
            selected_id = explicit_profile
            profile = self.document.profiles[selected_id]
            reason = "explicit X-Agent-UI-Experience override"
        elif requested_model in self.document.profiles:
            selected_id = requested_model
            profile = self.document.profiles[selected_id]
            reason = "experience selected explicitly"
        elif requested_model in self.document.models:
            selected_id = requested_model
            direct_model = self.document.models[requested_model]
            profile = Profile(
                advertised=False,
                model=requested_model,
                capability=required_capability,
                description=f"Direct access to {direct_model.display_name or requested_model}",
            )
            reason = "registered model selected directly"
        else:
            raise UnknownModelError(f"unknown model or experience: {requested_model}")

        if profile.route == "auto":
            selected_id, auto_reason = self._resolve_auto(messages)
            profile = self.document.profiles[selected_id]
            reason = auto_reason

        model_id, selection_reason = self._select_model(selected_id, profile)
        model = self.document.models[model_id]
        backend = self.document.backends.get(model.backend)
        if backend is None or not backend.enabled:
            raise UnavailableExperienceError(
                f"model {model_id!r} references unavailable backend {model.backend!r}"
            )
        if required_capability and not model.has_capability(required_capability):
            raise UnavailableExperienceError(
                f"model {model_id!r} does not declare endpoint capability "
                f"{required_capability!r}"
            )
        return ResolvedProfile(
            requested_model=requested_model,
            profile_id=selected_id,
            profile=profile,
            backend_model=model_id,
            route_reason=f"{reason}; {selection_reason}",
            model=model,
            backend_id=model.backend,
            backend=backend,
        )
