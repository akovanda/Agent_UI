from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RouteDecision:
    profile_id: str
    reason: str
    score: int


_STORY_STRONG = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcontinue (?:the|our|my) (?:story|campaign|scene|adventure)\b",
        r"\b(?:roleplay|role-play|in character|game master|dungeon master)\b",
        r"\bwrite (?:a|the) (?:scene|chapter|dialogue|monologue)\b",
        r"\b(?:campaign|worldbuilding|world-building|character sheet|lorebook)\b",
        r"\b(?:narrate|narrative prose|fictional scene)\b",
    )
]
_STORY_WEAK = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:story|scene|character|dialogue|prose|chapter|plot|setting|adventure)\b",
        r"\b(?:star wars|rpg|ttrpg|gm)\b",
    )
]
_TECHNICAL = re.compile(
    r"\b(?:debug|code|docker|kubernetes|network|vlan|api|database|server|linux|python|scala|"
    r"typescript|github|pull request|compile|test|benchmark|gpu|model|llama\.cpp|"
    r"error|stack trace)\b",
    re.IGNORECASE,
)


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


def choose_automatic_profile(messages: list[dict[str, Any]]) -> RouteDecision:
    text = latest_user_text(messages).strip()
    lowered = text.lower()
    if lowered.startswith("/story"):
        return RouteDecision("storyteller", "explicit /story control prefix", 100)
    if lowered.startswith(("/assistant", "/general")):
        return RouteDecision("assistant", "explicit general-assistant control prefix", -100)

    score = 0
    strong_hits = sum(bool(pattern.search(text)) for pattern in _STORY_STRONG)
    weak_hits = sum(bool(pattern.search(text)) for pattern in _STORY_WEAK)
    score += strong_hits * 3
    score += weak_hits
    if _TECHNICAL.search(text):
        score -= 2

    if score >= 3:
        return RouteDecision(
            "storyteller",
            f"creative/story intent score {score} ({strong_hits} strong, {weak_hits} weak)",
            score,
        )
    return RouteDecision(
        "assistant",
        f"default/general intent score {score} ({strong_hits} strong, {weak_hits} weak)",
        score,
    )


def remove_control_prefix(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove a leading /story, /assistant, or /general directive from the latest user text."""
    result = deepcopy(messages)
    prefix = re.compile(r"^\s*/(?:story|assistant|general)\b\s*", re.IGNORECASE)
    for message in reversed(result):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = prefix.sub("", content)
            break
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") not in {"text", "input_text"}:
                    continue
                value = part.get("text")
                if isinstance(value, str):
                    part["text"] = prefix.sub("", value)
                    break
            break
    return result
