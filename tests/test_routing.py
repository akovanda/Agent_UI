from local_ai_hub.routing import choose_automatic_profile, remove_control_prefix


def test_routes_explicit_story_prefix() -> None:
    messages = [{"role": "user", "content": "/story Continue our Endor campaign."}]
    decision = choose_automatic_profile(messages)
    assert decision.profile_id == "storyteller"
    assert "control prefix" in decision.reason
    assert remove_control_prefix(messages)[0]["content"] == "Continue our Endor campaign."


def test_routes_technical_question_to_assistant() -> None:
    messages = [
        {
            "role": "user",
            "content": "Debug this Docker API server; a story field in the JSON is failing.",
        }
    ]
    assert choose_automatic_profile(messages).profile_id == "assistant"


def test_routes_narrative_scene_to_storyteller() -> None:
    messages = [
        {
            "role": "user",
            "content": "Write a scene with character dialogue and continue our campaign.",
        }
    ]
    decision = choose_automatic_profile(messages)
    assert decision.profile_id == "storyteller"
    assert decision.score >= 3


def test_multimodal_text_and_general_prefix() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "/assistant Explain this character encoding bug."}
            ],
        }
    ]
    decision = choose_automatic_profile(messages)
    assert decision.profile_id == "assistant"
    cleaned = remove_control_prefix(messages)
    assert cleaned[0]["content"][0]["text"] == "Explain this character encoding bug."
    assert messages[0]["content"][0]["text"].startswith("/assistant")
