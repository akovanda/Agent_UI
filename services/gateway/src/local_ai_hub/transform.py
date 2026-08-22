from __future__ import annotations

from copy import deepcopy
from typing import Any

from .memory import MemoryRecord, render_memory_context
from .profiles import ResolvedProfile
from .routing import remove_control_prefix


class InvalidChatRequest(ValueError):
    pass


def _apply_reasoning(
    payload: dict[str, Any],
    resolved: ResolvedProfile,
    reasoning_override: str | None,
) -> None:
    requested = payload.pop("reasoning_effort", None)
    effort = reasoning_override or requested or resolved.profile.reasoning_effort
    if effort is None:
        return
    if not isinstance(effort, str) or not effort:
        raise InvalidChatRequest("reasoning effort must be a non-empty string")

    feature = resolved.model.features.reasoning
    if feature is None or not feature.supported:
        policy = feature.unsupported_policy if feature else "ignore"
        if policy == "reject":
            raise InvalidChatRequest(
                f"model {resolved.backend_model!r} does not support reasoning effort"
            )
        return

    if feature.values:
        if effort not in feature.values:
            accepted = ", ".join(sorted(feature.values))
            raise InvalidChatRequest(
                f"unsupported reasoning effort {effort!r}; accepted values: {accepted}"
            )
        mapped: Any = feature.values[effort]
    else:
        mapped = effort

    if mapped is None or mapped is False:
        return
    if feature.transport == "chat_template_kwargs":
        kwargs = payload.setdefault("chat_template_kwargs", {})
        if not isinstance(kwargs, dict):
            raise InvalidChatRequest("chat_template_kwargs must be an object")
        kwargs[feature.request_field] = mapped
    else:
        payload[feature.request_field] = mapped


def prepare_chat_payload(
    raw: dict[str, Any],
    resolved: ResolvedProfile,
    memories: list[MemoryRecord],
    memory_max_chars: int,
    reasoning_override: str | None = None,
) -> dict[str, Any]:
    payload = deepcopy(raw)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
        raise InvalidChatRequest("messages must be an array of objects")
    messages = remove_control_prefix(messages)

    for key, value in resolved.profile.defaults.items():
        payload.setdefault(key, value)
    _apply_reasoning(payload, resolved, reasoning_override)

    instruction_sections: list[str] = []
    if resolved.profile.system_prompt:
        instruction_sections.append(
            _section("Experience instructions", resolved.profile.system_prompt.strip())
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
        configured_role = resolved.model.metadata.get("instruction_role", "system")
        instruction_role = (
            configured_role if configured_role in {"system", "developer"} else "system"
        )
        conversation_messages.insert(
            0,
            {
                "role": instruction_role,
                "content": "\n\n".join(instruction_sections),
            },
        )

    payload["messages"] = conversation_messages
    payload["model"] = resolved.model.upstream_model or resolved.backend_model
    return payload


def prepare_passthrough_payload(
    raw: dict[str, Any],
    resolved: ResolvedProfile,
    reasoning_override: str | None = None,
) -> dict[str, Any]:
    """Apply generic defaults/model mapping to non-chat OpenAI-compatible calls."""

    payload = deepcopy(raw)
    for key, value in resolved.profile.defaults.items():
        payload.setdefault(key, value)
    _apply_reasoning(payload, resolved, reasoning_override)
    payload["model"] = resolved.model.upstream_model or resolved.backend_model
    return payload


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
