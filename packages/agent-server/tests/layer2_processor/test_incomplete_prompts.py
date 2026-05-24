"""Incomplete-utterance handling.

Contract: the LLM still CLASSIFIES user voice turns as
``complete`` / ``incomplete_short`` / ``incomplete_long`` via
``user_turn_status`` on the schema. When classified incomplete, the
processor stays QUIET for that round — no speech, no tool dispatch,
no demonstration state changes.

What's NOT here anymore: a follow-up timer that nudges the agent
("still with me?") after N seconds of silence. The widget is a
passive listener on a website, not an active call. If the visitor
trails off, we just leave it; they can resume on their own."""

from __future__ import annotations


async def test_incomplete_short_suppresses_reply(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "incomplete_short",
                "speech": "",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            }
        ]
    )
    await h.send_user("and then I was thinking…")
    # No speech pushed — incomplete classifications stay quiet.
    assert not h.assistant_speech_history
    # No tool dispatch.
    assert sum(1 for k, _ in h.batch_events if k == "dispatch") == 0
    # No active demo, in-flight, or pending state.
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_incomplete_long_suppresses_reply(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "incomplete_long",
                "speech": "",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            }
        ]
    )
    await h.send_user("hmm…")
    assert not h.assistant_speech_history
    h.assert_no_active_demo()


async def test_incomplete_voice_does_not_count_as_valid_for_idle_timer(
    harness_with_tools,
):
    """An incomplete fragment isn't a complete turn — it should NOT
    cancel a running idle timer. The visitor hasn't actually said a
    full thing TO us yet."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            # Kickoff (greeting) arms the idle timer.
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            # Incomplete user turn — must NOT cancel the timer.
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "incomplete_short",
                "speech": "",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_kickoff()
    pre_task = h.processor._idle_warning_task
    assert pre_task is not None and not pre_task.done()

    await h.send_user("and then maybe…")
    # Same task still running — incomplete didn't cancel it.
    assert h.processor._idle_warning_task is pre_task
    assert not pre_task.done()


async def test_text_wake_schema_does_not_include_user_turn_status(harness_with_tools):
    """Text wakes are always complete by construction; the
    user_turn_status field doesn't apply, schema must omit it."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "speech": "ok",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            }
        ]
    )
    await h.send_text_message("typed input")
    wrapped = h.llm.structured_views[0].schema
    assert "user_turn_status" not in wrapped["parameters"]["properties"]


async def test_incomplete_classification_still_in_voice_schema(harness_with_tools):
    """Classification itself is preserved — the schema for voice
    wakes still requires user_turn_status. Only the post-classification
    follow-up timer was removed."""
    from brain.agent_output import build_in_app_schema

    h = harness_with_tools
    s = build_in_app_schema(
        wake_mode="user_voice", batch_state="idle", tools=h.config.tools,
    )
    assert "user_turn_status" in s["properties"]
    assert "user_turn_status" in s["required"]
    enum = s["properties"]["user_turn_status"]["enum"]
    assert sorted(enum) == sorted(["complete", "incomplete_short", "incomplete_long"])
