"""Sanity-check that the harness can drive the processor through a
trivial round and observe the expected side effects."""

from __future__ import annotations


async def test_kickoff_greeting_no_tools(harness):
    harness.script_llm_outputs(
        [
            {
                "speech": "Hi! How can I help?",
                "demonstration_action": "continue",
                "demonstration_name": None,
            }
        ]
    )
    await harness.send_kickoff()
    assert "Hi! How can I help?" in (harness.assistant_speech_history[-1]["content"])
    harness.assert_no_active_demo()
    harness.assert_no_in_flight()
    harness.assert_no_pending()


async def test_user_message_plain_reply(harness):
    harness.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Sure thing.",
                "demonstration_action": "continue",
                "demonstration_name": None,
            }
        ]
    )
    await harness.send_user("hello")
    last = harness.assistant_speech_history[-1]
    assert last["role"] == "assistant"
    assert "Sure thing." in last["content"]


async def test_text_message_routes_through_text_wake(harness):
    harness.script_llm_outputs(
        [
            {
                "speech": "Got it.",
                "demonstration_action": "continue",
                "demonstration_name": None,
            }
        ]
    )
    await harness.send_text_message("typed input")
    # The first call's schema should NOT include user_turn_status (text wake).
    wrapped = harness.llm.structured_views[0].schema
    assert "user_turn_status" not in wrapped["parameters"]["properties"]
