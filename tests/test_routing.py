from __future__ import annotations

from local_ai_hub.config import RouteRule
from local_ai_hub.routing import (
    choose_automatic_profile,
    latest_user_text,
    remove_control_prefix,
    route_prefixes,
)


def rules() -> list[RouteRule]:
    return [
        RouteRule(profile="story", priority=100, prefixes=["/story"], patterns=[r"\bstory\b"]),
        RouteRule(profile="code", priority=90, prefixes=["/code"], patterns=[r"\bdebug\b"]),
        RouteRule(profile="chat", priority=0, prefixes=["/chat"]),
    ]


def test_explicit_prefix_wins() -> None:
    decision = choose_automatic_profile(
        [{"role": "user", "content": "/code debug this"}],
        rules(),
        {"chat", "code", "story"},
    )
    assert decision.profile_id == "code"
    assert decision.score > 10000


def test_unavailable_rule_is_skipped() -> None:
    decision = choose_automatic_profile(
        [{"role": "user", "content": "Continue the story"}],
        rules(),
        {"chat", "code"},
    )
    assert decision.profile_id == "chat"


def test_default_is_first_available_when_chat_missing() -> None:
    decision = choose_automatic_profile(
        [{"role": "user", "content": "Hello"}],
        rules(),
        {"code"},
    )
    assert decision.profile_id == "code"


def test_control_prefix_is_removed_from_text_and_multimodal_content() -> None:
    prefixes = route_prefixes(rules())
    text = remove_control_prefix(
        [{"role": "user", "content": "/story Continue"}], prefixes
    )
    mixed = remove_control_prefix(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "/code Inspect this"},
                    {"type": "image_url", "image_url": {"url": "x"}},
                ],
            }
        ],
        prefixes,
    )
    assert text[0]["content"] == "Continue"
    assert mixed[0]["content"][0]["text"] == "Inspect this"
    assert mixed[0]["content"][1]["type"] == "image_url"


def test_latest_user_text_reads_text_parts() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {"role": "assistant", "content": "response"},
    ]
    assert latest_user_text(messages) == "hello"
