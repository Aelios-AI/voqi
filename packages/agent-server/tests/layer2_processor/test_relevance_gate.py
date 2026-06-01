"""Voice-only relevance gate: the LLM tags whether a transcribed
utterance was actually addressed to the agent. Off-topic voice (room
chatter, side conversations, ambient noise) must be silently dropped
WITHOUT touching demo state or the idle-session machinery. Text wakes
bypass the gate entirely — typed text is always for the agent."""

from __future__ import annotations


async def test_off_topic_voice_speaks_brief_ack_but_drops_actions(
    harness_with_tools,
):
    """When voice is classified `off_topic`, the LLM's speech is
    preserved as a brief visitor-facing acknowledgment (so the agent
    doesn't ghost the visitor), but every side-effect field —
    tool_invocations, demonstration_action='start_new',
    demonstration_name — must be dropped. Scripting non-empty side
    effects proves the gate actually suppresses; scripting empty would
    be tautological."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "is_message_relevant": "off_topic",
                "user_turn_status": "complete",
                "speech": "Sure, opening the list for you.",
                "demonstration_action": "start_new",
                "demonstration_name": "browse-list",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("...just a sec, gimme the file")
    # Speech preserved — it's the brief acknowledgment.
    assert any(
        s["role"] == "assistant" and "opening the list" in s["content"]
        for s in h.assistant_speech_history
    )
    # No demo created, no batch in flight.
    h.assert_no_active_demo()
    h.assert_no_in_flight()
    # The tool_invocations were dropped — no dispatch happened.
    assert sum(1 for k, _ in h.batch_events if k == "dispatch") == 0


async def test_off_topic_voice_does_not_touch_active_demo(harness_with_tools):
    """A demo is mid-flight; voice picked up an off_topic fragment.
    The demo and its in-flight batch must be untouched."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "okay",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            {
                # Off-topic voice while batch is in flight.
                "is_message_relevant": "off_topic",
                "user_turn_status": "complete",
                "speech": "",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("show me my tasks")
    h.assert_active_demo(name="browse")
    h.assert_in_flight(expected_size=1)
    pre_id = h.processor._in_flight_batch.batch_id

    await h.send_user("hey grab me a coffee")
    # In-flight batch unchanged; demo unchanged.
    h.assert_active_demo(name="browse")
    h.assert_in_flight(expected_size=1)
    assert h.processor._in_flight_batch.batch_id == pre_id


async def test_text_wake_bypasses_relevance_gate(harness_with_tools):
    """Text wakes don't have is_message_relevant in the schema; the
    processor must NOT suppress on text even if the (defaulted-None)
    field is read by accident."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                # Note: NO is_message_relevant key — schema doesn't expose it.
                "speech": "On it.",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_text_message("show me my tasks")
    h.assert_active_demo(name="browse")
    h.assert_in_flight(expected_size=1)
