from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Profile, ProfileDocument
from .routing import RouteDecision, choose_automatic_profile


class UnknownModelError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    requested_model: str
    profile_id: str
    profile: Profile
    backend_model: str
    route_reason: str


class ProfileRegistry:
    def __init__(self, document: ProfileDocument):
        self.document = document

    def advertised_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        for profile_id, profile in self.document.profiles.items():
            if not profile.advertised:
                continue
            models.append(
                {
                    "id": profile_id,
                    "object": "model",
                    "owned_by": "local-ai-hub",
                    "permission": [],
                    "metadata": {
                        "description": profile.description,
                        "route": profile.route,
                        "backend_model": profile.backend_model,
                    },
                }
            )
        return models

    def resolve(
        self,
        requested_model: str,
        messages: list[dict[str, Any]],
        explicit_profile: str | None = None,
    ) -> ResolvedProfile:
        if explicit_profile:
            if explicit_profile not in self.document.profiles:
                raise UnknownModelError(f"unknown profile override: {explicit_profile}")
            selected_id = explicit_profile
            reason = "explicit X-Local-AI-Profile override"
        else:
            if requested_model not in self.document.profiles:
                raise UnknownModelError(f"unknown model/profile: {requested_model}")
            requested_profile = self.document.profiles[requested_model]
            if requested_profile.route == "auto":
                decision: RouteDecision = choose_automatic_profile(messages)
                selected_id = decision.profile_id
                reason = decision.reason
            else:
                selected_id = requested_model
                reason = "model selected explicitly"

        selected = self.document.profiles[selected_id]
        if selected.route == "auto" or not selected.backend_model:
            raise UnknownModelError(f"profile {selected_id} did not resolve to a backend model")
        return ResolvedProfile(
            requested_model=requested_model,
            profile_id=selected_id,
            profile=selected,
            backend_model=selected.backend_model,
            route_reason=reason,
        )
