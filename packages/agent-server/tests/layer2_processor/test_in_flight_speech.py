"""Visitor speaks while a batch is in flight.

Permission-to-interrupt rule: ambiguous request → continue + speech;
clear interrupt → start_new; chit-chat → continue + speech."""

from __future__ import annotations


async def _start_demo_with_in_flight_batch(h):
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Listing now.",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("show me my tasks")
    h.assert_in_flight()


async def test_ambiguous_interrupt_keeps_batch_running(harness_with_tools):
    h = harness_with_tools
    await _start_demo_with_in_flight_batch(h)

    # User says ambiguous "it'd be nice to also see analytics"
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "We're in the middle of browse — want me to stop and switch?",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            }
        ]
    )
    await h.send_user("it'd be nice to also see analytics")
    # Batch still in flight, no new dispatch.
    h.assert_in_flight()
    assert sum(1 for k, _ in h.batch_events if k == "dispatch") == 1


async def test_clear_interrupt_triggers_start_new(harness_with_tools):
    h = harness_with_tools
    await _start_demo_with_in_flight_batch(h)
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Switching to analytics.",
                "demonstration_action": "start_new",
                "demonstration_name": "analytics",
                "tool_invocations": [{"name": "get_status", "arguments": {}}],
            }
        ]
    )
    await h.send_user("stop this and show me analytics now")
    h.assert_active_demo(name="analytics", batches_dispatched=1)
    h.assert_in_flight()
    # Two dispatches recorded.
    assert sum(1 for k, _ in h.batch_events if k == "dispatch") == 2


# The "chit-chat during in-flight → no dispatch" case is the same code
# path as `test_ambiguous_interrupt_keeps_batch_running` (both:
# continue + no tools + speech-only → no second dispatch). Removed to
# avoid a same-file duplicate.


async def test_in_flight_state_context_includes_batch_purpose_guidance(
    harness_with_tools,
):
    h = harness_with_tools
    await _start_demo_with_in_flight_batch(h)
    # Pass the wake_mode the LLM actually sees when the visitor speaks
    # mid-demo: user_voice. The mid-demonstration guidance block is
    # rendered for that case.
    msg = h.processor._build_state_context_message(wake_mode="user_voice")
    assert "IN FLIGHT" in msg
    assert "ambiguous" in msg.lower()
    assert "permission" in msg.lower() or "go-ahead" in msg.lower()
