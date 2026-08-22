from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .config import RouteRule


@dataclass(frozen=True, slots=True)
class RouteDecision:
    profile_id: str
    reason: str
    score: int


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    return ""


def latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _text_content(message.get("content"))
    return ""


def choose_automatic_profile(
    messages: list[dict[str, Any]],
    rules: list[RouteRule],
    available_profiles: set[str],
    default_profile: str = "chat",
) -> RouteDecision:
    if not available_profiles:
        raise ValueError("no chat profiles currently have a compatible model")

    text = latest_user_text(messages).strip()
    lowered = text.lower()
    best: RouteDecision | None = None
    for rule in sorted(rules, key=lambda item: item.priority, reverse=True):
        if rule.profile not in available_profiles:
            continue
        for prefix in rule.prefixes:
            if lowered.startswith(prefix.lower()):
                return RouteDecision(
                    rule.profile,
                    f"explicit {prefix} route prefix",
                    10000 + rule.priority,
                )
        hits = 0
        for pattern in rule.patterns:
            try:
                hits += int(bool(re.search(pattern, text, re.IGNORECASE)))
            except re.error:
                continue
        if hits:
            score = rule.priority + hits
            candidate = RouteDecision(
                rule.profile,
                f"registry route rule matched {hits} pattern(s)",
                score,
            )
            if best is None or candidate.score > best.score:
                best = candidate

    if best is not None:
        return best
    if default_profile in available_profiles:
        return RouteDecision(default_profile, "default available chat profile", 0)
    selected = sorted(available_profiles)[0]
    return RouteDecision(selected, "first available chat profile", -1)


def route_prefixes(rules: list[RouteRule]) -> list[str]:
    return [prefix for rule in rules for prefix in rule.prefixes]


def remove_control_prefix(
    messages: list[dict[str, Any]], prefixes: list[str] | None = None
) -> list[dict[str, Any]]:
    """Remove a configured route directive from the latest user text."""

    result = deepcopy(messages)
    values = [value for value in (prefixes or []) if value]
    if not values:
        return result
    alternatives = "|".join(re.escape(value.lstrip("/")) for value in values)
    prefix_pattern = re.compile(rf"^\s*/(?:{alternatives})\b\s*", re.IGNORECASE)
    for message in reversed(result):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = prefix_pattern.sub("", content)
            break
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") not in {
                    "text",
                    "input_text",
                }:
                    continue
                value = part.get("text")
                if isinstance(value, str):
                    part["text"] = prefix_pattern.sub("", value)
                    break
            break
    return result
