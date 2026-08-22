from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .memory import MemoryRecord, render_memory_context
from .profiles import ResolvedProfile
from .routing import remove_control_prefix


class InvalidChatRequest(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedChat:
    payload: dict[str, Any]
    requested_effort: str | None
    applied_effort: str | None


def _requested_reasoning(payload: dict[str, Any], override: str | None) -> tuple[str | None, bool]:
    if override:
        return override, True
    flat = payload.pop("reasoning_effort", None)
    if flat is not None:
        if not isinstance(flat, str):
            raise InvalidChatRequest("reasoning_effort must be a string")
        return flat, True
    nested = payload.get("reasoning")
    if isinstance(nested, dict) and nested.get("effort") is not None:
        effort = nested.get("effort")
        if not isinstance(effort, str):
            raise InvalidChatRequest("reasoning.effort must be a string")
        return effort, True
    return None, False


def _apply_reasoning(
    payload: dict[str, Any],
    resolved: ResolvedProfile,
    override: str | None,
) -> tuple[str | None, str | None]:
    requested, explicit = _requested_reasoning(payload, override)
    preference = requested or resolved.profile.reasoning_effort
    support = resolved.model.features.reasoning
    if preference in {None, "none"}:
        payload.pop("reasoning_effort", None)
        if support.transport != "object":
            payload.pop("reasoning", None)
        return preference, None

    normalized = support.aliases.get(preference, preference)
    if support.transport == "none":
        if explicit:
            raise InvalidChatRequest(
                f"model {resolved.model_id!r} does not advertise reasoning-effort support"
            )
        payload.pop("reasoning", None)
        return preference, None
    if support.levels and normalized not in support.levels:
        if explicit:
            raise InvalidChatRequest(
                f"reasoning effort {preference!r} is unsupported; choose one of {support.levels}"
            )
        normalized = support.default if support.default in support.levels else None
    if normalized is None:
        return preference, None

    if support.transport == "flat":
        payload.pop("reasoning", None)
        payload[support.field] = normalized
    elif support.transport == "object":
        value = payload.get(support.field)
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise InvalidChatRequest(f"{support.field} must be an object")
        value = dict(value)
        value["effort"] = normalized
        payload[support.field] = value
    elif support.transport == "chat_template":
        payload.pop("reasoning", None)
        kwargs = payload.setdefault("chat_template_kwargs", {})
        if not isinstance(kwargs, dict):
            raise InvalidChatRequest("chat_template_kwargs must be an object")
        kwargs[support.field] = normalized
    return preference, normalized


def prepare_chat_payload(
    raw: dict[str, Any],
    resolved: ResolvedProfile,
    memories: list[MemoryRecord],
    memory_max_chars: int,
    reasoning_override: str | None = None,
    route_prefixes: list[str] | None = None,
) -> PreparedChat:
    payload = deepcopy(raw)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
        raise InvalidChatRequest("messages must be an array of objects")
    messages = remove_control_prefix(messages, route_prefixes)

    for key, value in resolved.profile.defaults.items():
        payload.setdefault(key, value)

    requested_effort, applied_effort = _apply_reasoning(
        payload, resolved, reasoning_override
    )

    instruction_sections: list[str] = []
    if resolved.profile.system_prompt:
        instruction_sections.append(
            _section("Local profile instructions", resolved.profile.system_prompt.strip())
        )

    conversation_messages: list[dict[str, Any]] = []
    client_instructions: list[str] = []
    for message in messages:
        role = message.get("role")
        if role in {"system", "developer"}:
            content = _instruction_content(message)
            if content:
                client_instructions.append(content)
            continue
        conversation_messages.append(message)

    if client_instructions:
        instruction_sections.append(
            _section("Client-provided instructions", "\n\n".join(client_instructions))
        )

    memory_context = render_memory_context(memories, memory_max_chars)
    if memory_context:
        instruction_sections.append(_section("Retrieved memory", memory_context))

    if instruction_sections:
        instruction_role = "developer" if resolved.model.features.developer_role else "system"
        conversation_messages.insert(
            0,
            {"role": instruction_role, "content": "\n\n".join(instruction_sections)},
        )

    payload["messages"] = conversation_messages
    payload["model"] = resolved.upstream_model
    return PreparedChat(
        payload=payload,
        requested_effort=requested_effort,
        applied_effort=applied_effort,
    )


def _section(title: str, content: str) -> str:
    return f"# {title}\n\n{content.strip()}"


def _instruction_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
        if text_parts:
            return "\n".join(text_parts)
    raise InvalidChatRequest("system and developer message content must contain text")
