from __future__ import annotations

from copy import deepcopy
from typing import Any

from .memory import MemoryRecord, render_memory_context
from .profiles import ResolvedProfile
from .routing import remove_control_prefix


class InvalidChatRequest(ValueError):
    pass


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

    requested_effort = payload.pop("reasoning_effort", None)
    effort = reasoning_override or requested_effort or resolved.profile.reasoning_effort
    if effort not in {None, "low", "medium", "high"}:
        raise InvalidChatRequest("reasoning_effort must be low, medium, or high")

    is_gpt_oss = resolved.backend_model.startswith("gpt-oss")
    if is_gpt_oss and effort:
        # llama.cpp maps this OpenAI-compatible field into the GPT-OSS Harmony
        # template's reasoning_effort argument. Keeping it structured avoids
        # accidentally consuming the only developer-message slot with a
        # hand-written "Reasoning:" system message.
        payload["reasoning_effort"] = effort

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
        instruction_role = "developer" if is_gpt_oss else "system"
        conversation_messages.insert(
            0,
            {
                "role": instruction_role,
                "content": "\n\n".join(instruction_sections),
            },
        )

    payload["messages"] = conversation_messages
    payload["model"] = resolved.backend_model
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
