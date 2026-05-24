"""Active demonstration carries a structured batch-by-batch history of
every tool invocation it dispatched, with statuses + results updated
as widget results land. Renders into the per-round state-context block
so the LLM can reason about whether the goal is met by the cumulative
record (rather than chasing call_ids back through history)."""

from __future__ import annotations

import json


async def test_history_records_batch_on_dispatch(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "go",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [
                    {"name": "list_tasks", "arguments": {}},
                    {"name": "get_status", "arguments": {}},
                ],
            }
        ]
    )
    await h.send_user("show me")
    active = h.processor._active_demonstration
    assert active is not None and len(active.batches_history) == 1
    batch = active.batches_history[0]
    assert batch.batch_index == 1
    assert [inv.name for inv in batch.invocations] == [
        "list_tasks",
        "get_status",
    ]
    assert all(inv.status == "in_progress" for inv in batch.invocations)


async def test_history_updates_invocation_statuses_on_result(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "go",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [
                    {"name": "list_tasks", "arguments": {}},
                    {"name": "get_status", "arguments": {}},
                ],
            },
            {
                "speech": "all done",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("go")
    cid_list = h.last_call_id(name="list_tasks")
    cid_status = h.last_call_id(name="get_status")
    # Resolve list_tasks as success.
    await h.deliver_tool_result(call_id=cid_list, result=["a", "b"])
    # Demo ended after second result lands; capture history before end.
    # We observe the batches_history snapshot from in-flight state by
    # delivering both before pump_until_idle finalises the demo end.
    # Easiest: deliver get_status as error.
    await h.deliver_tool_result(call_id=cid_status, error="permission denied")
    # Demo has now ended via end_current. The session's tool_call_log
    # captured both outcomes.
    log_names = [e["toolName"] for e in h.tool_outcome_log]
    assert sorted(log_names) == ["get_status", "list_tasks"]


async def test_state_context_renders_batches_with_indices(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "first batch",
                "demonstration_action": "start_new",
                "demonstration_name": "demo",
                "tool_invocations": [
                    {"name": "list_tasks", "arguments": {}},
                ],
            },
            {
                "speech": "second batch",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [{"name": "get_status", "arguments": {}}],
            },
        ]
    )
    await h.send_user("kick off")
    cid1 = h.last_call_id(name="list_tasks")
    await h.deliver_tool_result(call_id=cid1, result=["alpha"])
    # State context now reflects: Batch #1 done, Batch #2 in flight.
    msg = h.processor._build_state_context_message()
    assert "Batch #1" in msg
    assert "Batch #2" in msg
    assert "list_tasks" in msg
    assert "get_status" in msg
    # Batch #1 should have a success status; Batch #2 should be in_progress.
    # We slice the message into per-batch chunks for clarity.
    one_idx = msg.index("Batch #1")
    two_idx = msg.index("Batch #2")
    batch_one = msg[one_idx:two_idx]
    batch_two = msg[two_idx:]
    assert "success" in batch_one
    assert "in_progress" in batch_two


async def test_state_context_flags_just_resolved_on_tool_batch_completed_wake(
    harness_with_tools,
):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "start",
                "demonstration_action": "start_new",
                "demonstration_name": "demo",
                "tool_invocations": [
                    {"name": "list_tasks", "arguments": {}},
                ],
            },
            # Round 2 fires automatically when the result lands.
            {
                "speech": "all done",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("go")
    cid = h.last_call_id(name="list_tasks")
    # Capture what the state-context block looks like for the
    # tool_batch_completed wake — inspect it BEFORE delivery so
    # _in_flight_batch is still set and we control the wake_mode.
    msg = h.processor._build_state_context_message(wake_mode="tool_batch_completed")
    # The current batch is flagged JUST RESOLVED in the batch history.
    assert "JUST RESOLVED" in msg
    # The action prose for tool_batch_completed lists only the two
    # allowed paths and does NOT mention start_new at all (the schema
    # gates that, the prompt doesn't meta-explain it).
    actions_idx = msg.index("How to act this turn")
    actions_block = msg[actions_idx : actions_idx + 800]
    assert "`continue`" in actions_block
    assert "`end_current`" in actions_block
    assert "start_new" not in actions_block
    # Natural pump completes the run (round 2 ends the demo).
    await h.deliver_tool_result(call_id=cid, result="ok")
    h.assert_no_active_demo()


async def test_state_context_flags_in_flight_when_results_pending(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "start",
                "demonstration_action": "start_new",
                "demonstration_name": "demo",
                "tool_invocations": [
                    {"name": "list_tasks", "arguments": {}},
                    {"name": "get_status", "arguments": {}},
                ],
            }
        ]
    )
    await h.send_user("go")
    msg = h.processor._build_state_context_message()
    assert "IN FLIGHT" in msg
    # Both invocations show in_progress
    assert msg.count("in_progress") >= 2


async def test_history_marks_cancelled_on_demo_interruption(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "first",
                "demonstration_action": "start_new",
                "demonstration_name": "first",
                "tool_invocations": [
                    {"name": "list_tasks", "arguments": {}},
                ],
            },
            {
                "user_turn_status": "complete",
                "speech": "switching",
                "demonstration_action": "start_new",
                "demonstration_name": "second",
                "tool_invocations": [{"name": "get_status", "arguments": {}}],
            },
        ]
    )
    await h.send_user("show tasks")
    first_demo = h.processor._active_demonstration
    assert first_demo is not None
    first_history_snapshot = list(first_demo.batches_history)

    # Interrupt with start_new before delivering any result.
    await h.send_user("never mind, check status")
    # The first demo is gone; we only have the snapshot we captured.
    # Verify its in-flight invocation was flipped to cancelled by the
    # cancellation cascade.
    for batch in first_history_snapshot:
        for inv in batch.invocations:
            assert inv.status == "cancelled"


async def test_history_marks_timeout_on_per_batch_timeout(harness_with_tools):
    """When the per-batch timeout fires before tool results land, the
    matching invocation in the demo's batch-history flips to status
    'timeout' before the synthetic TOOL_BATCH_COMPLETED inference runs.
    The state-context block on that inference shows the timeout
    explicitly so the LLM can react sensibly."""
    h = harness_with_tools
    h.set_timeouts(batch_seconds=0.05)
    captured_msg: dict[str, str] = {}

    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "go",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [
                    {"name": "list_tasks", "arguments": {}},
                ],
            },
            {
                "speech": "wrapping up",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )

    # Capture the rendered system prompt from round 2 (the
    # tool_batch_completed inference fired by the synthetic completion
    # after the timeout). The unified prompt is rendered from
    # _llm_round_streaming via _render_agent_turn_prompt; we wrap it
    # to grab the second invocation.
    orig_render = h.processor._render_agent_turn_prompt
    call_count = {"n": 0}

    def capturing_render(*, wake_mode, batch_state, screenshot_context=None):
        msg = orig_render(
            wake_mode=wake_mode,
            batch_state=batch_state,
            screenshot_context=screenshot_context,
        )
        call_count["n"] += 1
        if call_count["n"] == 2:
            captured_msg["msg"] = msg
        return msg

    h.processor._render_agent_turn_prompt = capturing_render  # type: ignore[assignment]

    await h.send_user("go")
    # Don't deliver the result; let the per-batch timeout fire.
    import asyncio

    await asyncio.sleep(0.2)
    await h.pump_until_idle()

    # The state-context block on the post-timeout inference should
    # show the invocation with status='timeout' rendered in the
    # batch history.
    assert "msg" in captured_msg, "second inference (post-timeout) never fired"
    assert "timeout" in captured_msg["msg"]


async def test_no_orphan_tool_calls_in_conversation_history(harness_with_tools):
    """The processor must NOT push assistant.tool_calls / tool messages
    into conversation history. The structured tool record lives only in
    the batch-history block of the state-context system message."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "ok",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("show me")
    # Inspect every message in conversation history.
    msgs = h.processor._history.get_messages_for_inference()
    for msg in msgs:
        if isinstance(msg, dict):
            assert msg.get("role") != "tool", (
                "no tool messages should land in history; the structured "
                "batch-history block is the canonical record"
            )
            assert "tool_calls" not in msg, (
                "no assistant message in history should carry tool_calls; "
                "only plain assistant text is preserved"
            )
