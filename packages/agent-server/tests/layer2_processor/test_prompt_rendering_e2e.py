"""End-to-end rendering of the unified agent-turn template.

Pins what the LLM actually sees on representative state combinations
so any future change to the template / state-context renderer surfaces
in a clear, structural assertion. The earlier test suite already
covers ``each section's precondition`` (test_state_context_sections);
THIS file covers ``the full multi-batch rendered prompt is shaped
correctly end to end``."""

from __future__ import annotations

from datetime import datetime, timezone

from brain.processor import (
    ActiveDemonstration,
    BatchHistoryEntry,
    InvocationHistoryEntry,
)


async def test_full_prompt_renders_three_batch_history_with_mixed_statuses(
    harness_with_tools,
):
    """Snapshot-style: assemble an active demo with 3 batches, each
    holding invocations in mixed terminal statuses (success, error,
    timeout, cancelled, in_progress), and verify the rendered prompt
    contains them all in dispatch order, properly tagged."""
    h = harness_with_tools

    # Hand-build a demo + history that the natural flow would take a
    # while to construct. We're testing the renderer, not the flow.
    demo = ActiveDemonstration(
        id="demo-1",
        name="create-task",
        started_at=datetime(2026, 5, 3, 10, 0, 0, tzinfo=timezone.utc),
        batches_history=[
            BatchHistoryEntry(
                batch_id="b1",
                batch_index=1,
                dispatched_at=datetime(2026, 5, 3, 10, 0, 5, tzinfo=timezone.utc),
                invocations=[
                    InvocationHistoryEntry(
                        call_id="c1", name="log_in", arguments={},
                        status="success", result={"user": "demo"},
                    ),
                ],
            ),
            BatchHistoryEntry(
                batch_id="b2",
                batch_index=2,
                dispatched_at=datetime(2026, 5, 3, 10, 0, 10, tzinfo=timezone.utc),
                invocations=[
                    InvocationHistoryEntry(
                        call_id="c2", name="list_tasks", arguments={},
                        status="error", result="permission denied",
                    ),
                    InvocationHistoryEntry(
                        call_id="c3", name="get_status", arguments={"id": "p"},
                        status="timeout", result="60s timeout",
                    ),
                ],
            ),
            BatchHistoryEntry(
                batch_id="b3",
                batch_index=3,
                dispatched_at=datetime(2026, 5, 3, 10, 0, 15, tzinfo=timezone.utc),
                invocations=[
                    InvocationHistoryEntry(
                        call_id="c4", name="create_task",
                        arguments={"name": "Refactor"},
                        status="in_progress", result=None,
                    ),
                ],
            ),
        ],
        tool_batches_dispatched=3,
    )
    h.processor._active_demonstration = demo
    # b3 is the in-flight batch.
    from brain.processor import InFlightBatch

    h.processor._in_flight_batch = InFlightBatch(
        demo_id="demo-1",
        batch_id="b3",
        expected_size=1,
        dispatched_at=datetime(2026, 5, 3, 10, 0, 15, tzinfo=timezone.utc),
        tool_names=["create_task"],
    )

    # Render under the user_voice wake (visitor speaks during in-flight).
    msg = h.processor._render_agent_turn_prompt(
        wake_mode="user_voice", batch_state="in_flight",
    )

    # All three batches appear, in dispatch order.
    assert "Batch #1" in msg
    assert "Batch #2" in msg
    assert "Batch #3" in msg
    one_idx = msg.index("Batch #1")
    two_idx = msg.index("Batch #2")
    three_idx = msg.index("Batch #3")
    assert one_idx < two_idx < three_idx

    # Batch #3 is flagged as in-flight (it's the current).
    batch_three_block = msg[three_idx:]
    assert "IN FLIGHT" in batch_three_block
    # Batch #1 is NOT flagged.
    batch_one_block = msg[one_idx:two_idx]
    assert "IN FLIGHT" not in batch_one_block
    assert "JUST RESOLVED" not in batch_one_block

    # Each invocation renders with name(arguments_json) → status: result
    assert "log_in({})" in msg
    assert "success" in msg
    assert "list_tasks({})" in msg
    assert "permission denied" in msg
    assert "get_status" in msg
    assert "timeout" in msg
    assert "create_task" in msg
    assert "in_progress" in msg

    # Mid-demonstration guidance block fires whenever a demo is active
    # on a user wake (in_flight or idle batch state both qualify).
    assert "Visitor spoke mid-demonstration" in msg
    # Allowed-actions block lists all three values for user wakes.
    assert "start_new" in msg
    assert "end_current" in msg
    # Two-trigger rule prose present.
    assert "Tool invocations" in msg
    # Tool list included.
    assert "REGISTERED TOOLS" in msg
    # Conversation-history section present (even if empty).
    assert "CONVERSATION SO FAR" in msg


async def test_tool_batch_completed_wake_flags_current_batch_as_just_resolved(
    harness_with_tools,
):
    """When the wake is tool_batch_completed, the current batch in
    history should be flagged JUST RESOLVED, not IN FLIGHT."""
    h = harness_with_tools
    demo = ActiveDemonstration(
        id="d", name="x",
        started_at=datetime(2026, 5, 3, 10, 0, 0, tzinfo=timezone.utc),
        batches_history=[
            BatchHistoryEntry(
                batch_id="b1", batch_index=1,
                dispatched_at=datetime(2026, 5, 3, 10, 0, 5, tzinfo=timezone.utc),
                invocations=[
                    InvocationHistoryEntry(
                        call_id="c1", name="list_tasks", arguments={},
                        status="success", result=["a", "b"],
                    ),
                ],
            ),
        ],
        tool_batches_dispatched=1,
    )
    h.processor._active_demonstration = demo
    from brain.processor import InFlightBatch

    h.processor._in_flight_batch = InFlightBatch(
        demo_id="d", batch_id="b1", expected_size=1,
        dispatched_at=datetime(2026, 5, 3, 10, 0, 5, tzinfo=timezone.utc),
        tool_names=["list_tasks"],
    )
    msg = h.processor._render_agent_turn_prompt(
        wake_mode="tool_batch_completed", batch_state="in_flight",
    )
    assert "JUST RESOLVED" in msg
    # IN FLIGHT must NOT appear as a flag this round (the batch's
    # results are in).
    one_idx = msg.index("Batch #1")
    batch_block = msg[one_idx : one_idx + 600]
    assert "IN FLIGHT" not in batch_block
    # Action prose lists the two options that ARE allowed on this wake.
    assert "`continue`" in msg and "`end_current`" in msg
    # And does NOT mention `start_new` as a path to take this turn.
    actions_idx = msg.index("How to act this turn")
    actions_block = msg[actions_idx : actions_idx + 800]
    assert "start_new" not in actions_block


async def test_pending_state_renders_proposed_batch_with_confirmable_flag(
    harness_with_tools,
):
    """Pending-confirmation state surfaces the proposed batch with the
    ``← REQUIRES CONFIRMATION`` flag on the right entries."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete-x",
                "tool_invocations": [
                    {"name": "delete_task", "arguments": {"id": "p1"}},
                    {"name": "send_email", "arguments": {"to": "owner"}},
                ],
            }
        ]
    )
    await h.send_user("delete it and notify owner")
    msg = h.processor._render_agent_turn_prompt(
        wake_mode="user_voice", batch_state="pending_confirmation",
    )
    # The proposed-batch list shows both invocations with the
    # confirmable flag on each (both are confirmable in our fixture).
    assert "Batch pending confirmation" in msg
    assert "delete_task" in msg
    assert "send_email" in msg
    # The flag appears at least twice (one per confirmable tool).
    assert msg.count("REQUIRES CONFIRMATION") >= 2
    # Pending-resolution guidance present.
    assert "accept" in msg and "replace" in msg and "keep_waiting" in msg


async def test_conversation_history_renders_user_assistant_turns(harness_with_tools):
    """The unified template's CONVERSATION SO FAR section renders the
    rolling user/assistant message log inline — no separate message
    list shipped to the LLM."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Hi! Sure thing.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            }
        ]
    )
    await h.send_user("hello")
    msg = h.processor._render_agent_turn_prompt(
        wake_mode="user_voice", batch_state="idle",
    )
    assert "CONVERSATION SO FAR" in msg
    # Both turns rendered as USER: ... / ASSISTANT: ... lines.
    assert "USER: hello" in msg
    assert "ASSISTANT: Hi! Sure thing." in msg


async def test_no_demo_no_batch_history_block_in_template(harness_with_tools):
    """When there's no active demo, the demonstration-state section
    says 'NO ACTIVE DEMONSTRATION' and there's no batch-history loop
    section."""
    h = harness_with_tools
    msg = h.processor._render_agent_turn_prompt(
        wake_mode="user_voice", batch_state="idle",
    )
    assert "No active demonstration" in msg
    assert "Batch-by-batch history" not in msg


async def test_all_static_sections_present_on_first_turn(harness_with_tools):
    """First-turn render: sanity that the static prefix sections all
    show up — refusal templates, software blurb, output language."""
    h = harness_with_tools
    msg = h.processor._render_agent_turn_prompt(
        wake_mode="user_voice", batch_state="idle",
    )
    assert "OUTPUT LANGUAGE" in msg
    assert "Always respond in English" in msg
    assert "IMPORTANT SAFETY AND SCOPE BOUNDARIES" in msg
    assert "REGISTERED TOOLS" in msg
    assert "GROUND RULES" in msg


async def test_two_message_send_shape_user_voice_is_text_only(
    harness_with_tools,
):
    """A vanilla user-voice round (LLM does NOT request a screenshot)
    sends exactly TWO messages: a system prompt + a plain-string
    human marker. The processor only fetches a screenshot when the
    LLM sets ``decision_to_request_screenshot=True``; on a regular
    answer turn it skips the fetch and the marker is a string."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "ok",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
                "decision_to_request_screenshot": False,
                "screenshot_request_context": None,
            }
        ]
    )
    await h.send_user("hi")
    sent = h.llm.structured_views[-1].calls[-1]["messages"]
    assert len(sent) == 2
    assert sent[0][0] == "system"
    assert sent[1][0] == "human"
    assert isinstance(sent[0][1], str) and len(sent[0][1]) > 100

    human_content = sent[1][1]
    assert isinstance(human_content, str), (
        "user-voice rounds without decision_to_request_screenshot do NOT "
        "fetch a screenshot — the human marker is a plain string, not "
        "a multimodal content-block list"
    )
    assert "process" in human_content.lower()
    # And: no fetch was issued at all.
    assert h.screenshots.requests == 0


async def test_two_message_send_shape_screenshot_result_is_multimodal(
    harness_with_tools,
):
    """On the SCREENSHOT_RESULT wake (after the LLM has asked for
    vision) the human marker is a multimodal content-block list with
    text + image_url. The contract: the image rides on the human
    marker, not in the system prompt."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            # Round 1 (user_voice): LLM asks for vision.
            {
                "user_turn_status": "complete",
                "is_message_relevant": "relevant",
                "speech": "Let me take a look.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
                "decision_to_request_screenshot": True,
                "screenshot_request_context": "Visitor pointed at something.",
            },
            # Round 2 (screenshot_result): the actual answer. THIS is
            # the round whose human marker we assert on.
            {
                "speech": "I see it.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("what is this?")
    sent = h.llm.structured_views[-1].calls[-1]["messages"]
    assert len(sent) == 2
    assert sent[0][0] == "system"
    assert sent[1][0] == "human"
    human_content = sent[1][1]
    assert isinstance(human_content, list), (
        "screenshot_result wake → human marker carries the captured "
        "bytes as a multimodal content-block list"
    )
    text_block, image_block = human_content
    assert text_block["type"] == "text"
    assert "process" in text_block["text"].lower()
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["url"].startswith("data:image/jpeg;base64,")
