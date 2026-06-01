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


async def test_incomplete_short_speaks_nudge_but_drops_actions(
    harness_with_tools,
):
    """When the LLM classifies a turn `incomplete_short`, its speech IS
    preserved (acts as a "still listening" nudge), but every side-effect
    field — tool_invocations, demonstration_action, demonstration_name
    — must be dropped. The LLM may emit them anyway on a misclassified
    round; the processor's job is to honour the classification by
    suppressing the actions while letting the warm nudge through."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "incomplete_short",
                "speech": "Sure, listing your tasks now — one sec.",
                # Side-effects below MUST all be dropped.
                "demonstration_action": "start_new",
                "demonstration_name": "list-them",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("and then I was thinking…")
    # Speech preserved — it's the visitor-facing nudge.
    assert any(
        s["role"] == "assistant" and "listing your tasks" in s["content"]
        for s in h.assistant_speech_history
    )
    # The tool invocations were dropped — no dispatch.
    assert sum(1 for k, _ in h.batch_events if k == "dispatch") == 0
    # demonstration_action='start_new' was ignored — no demo was created.
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_incomplete_long_speaks_nudge_but_drops_actions(
    harness_with_tools,
):
    """Same defence as `incomplete_short` for the `incomplete_long`
    branch. The two enum values share a code path; the pair pins that
    the branch handles both. Both script non-empty speech + a
    side-effect bundle so the assertions prove the right behaviour:
    speech through, actions dropped."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "incomplete_long",
                "speech": "I'll go ahead and delete that for you.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete-it",
                "tool_invocations": [
                    {"name": "delete_task", "arguments": {"id": "p1"}}
                ],
            }
        ]
    )
    await h.send_user("hmm so I was wondering if we could…")
    assert any(
        s["role"] == "assistant" and "delete that" in s["content"]
        for s in h.assistant_speech_history
    )
    assert sum(1 for k, _ in h.batch_events if k == "dispatch") == 0
    h.assert_no_active_demo()
    h.assert_no_in_flight()


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


# Schema shape for user_turn_status (present on voice, absent on text,
# enum values) is covered by:
#   - layer1_unit/test_schema.test_user_turn_status_present_only_on_user_voice
#   - layer1_unit/test_schema.test_user_turn_status_absent_on_other_wakes
# Two earlier tests here re-asserted those properties via the harness;
# removed because they duplicated layer-1 coverage without exercising
# any processor state.
