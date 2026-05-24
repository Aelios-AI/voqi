"""Stale-result discipline: the four guards in _on_tool_outcome and the
_handle_agent_turn batch_id staleness guard."""

from __future__ import annotations

import asyncio

from brain.tool_dispatcher import ToolDispatchOutcome


async def test_guard_1_stale_demo_id(harness_with_tools):
    """Late tool result for a demo that has been replaced is dropped."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "browse",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "create",
                "demonstration_action": "start_new",
                "demonstration_name": "create",
                "tool_invocations": [{"name": "create_task", "arguments": {"name": "X"}}],
            },
        ]
    )
    await h.send_user("browse")
    old_demo_id = h.processor._active_demonstration.id
    old_batch = h.processor._in_flight_batch.batch_id
    n_tool_log_before = len(h.tool_outcome_log)

    await h.send_user("nope, create one")
    # Now we manually invoke the outcome callback as if a stale result
    # for the old demo arrives. It must be dropped (no append to log).
    await h.processor._on_tool_outcome(
        old_demo_id, old_batch, "old-call", "list_tasks",
        {}, ToolDispatchOutcome.SUCCESS, ["a", "b"],
    )
    assert len(h.tool_outcome_log) == n_tool_log_before


async def test_guard_2_stale_batch_id(harness_with_tools):
    """Within the same demo, a result for a previous batch_id is dropped."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "browse",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            # After first batch resolves, another batch fires under same demo.
            {
                "speech": "next",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [{"name": "get_status", "arguments": {}}],
            },
        ]
    )
    await h.send_user("go")
    demo_id = h.processor._active_demonstration.id
    old_batch_id = h.processor._in_flight_batch.batch_id
    cid = h.last_call_id(name="list_tasks")
    await h.deliver_tool_result(call_id=cid, result="ok")
    new_batch_id = h.processor._in_flight_batch.batch_id
    assert new_batch_id != old_batch_id

    n_log_before = len(h.tool_outcome_log)
    await h.processor._on_tool_outcome(
        demo_id, old_batch_id, "ghost-call", "list_tasks",
        {}, ToolDispatchOutcome.SUCCESS, "stale",
    )
    # Guard 2 dropped it — log unchanged.
    assert len(h.tool_outcome_log) == n_log_before


async def test_guard_3_call_id_already_force_finalized(harness_with_tools):
    """A call_id absent from the registry indicates the call was already
    force-finalized. Late SUCCESS callback must be dropped to avoid a
    duplicate / overwrite in history."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "go",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("go")
    demo_id = h.processor._active_demonstration.id
    batch_id = h.processor._in_flight_batch.batch_id
    cid = h.last_call_id(name="list_tasks")

    # Simulate force-finalization: remove from registry directly.
    h.processor._tool_call_names.pop(cid, None)
    h.processor._tool_call_args.pop(cid, None)

    n_log_before = len(h.tool_outcome_log)
    await h.processor._on_tool_outcome(
        demo_id, batch_id, cid, "list_tasks",
        {}, ToolDispatchOutcome.SUCCESS, "late",
    )
    assert len(h.tool_outcome_log) == n_log_before


async def test_guard_4_no_in_flight_batch(harness_with_tools):
    """If there's no in-flight batch (already consumed / cancelled),
    a callback for it must drop."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "go",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            {
                "speech": "done",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("go")
    demo_id = h.processor._active_demonstration.id
    batch_id = h.processor._in_flight_batch.batch_id
    cid = h.last_call_id(name="list_tasks")
    await h.deliver_tool_result(call_id=cid, result="ok")
    h.assert_no_active_demo()
    h.assert_no_in_flight()

    n_log_before = len(h.tool_outcome_log)
    await h.processor._on_tool_outcome(
        demo_id, batch_id, "ghost", "list_tasks",
        {}, ToolDispatchOutcome.SUCCESS, "late",
    )
    assert len(h.tool_outcome_log) == n_log_before


async def test_stale_tool_batch_completed_frame_dropped(harness_with_tools):
    """A leftover TOOL_BATCH_COMPLETED frame for an old batch must be
    dropped by _handle_agent_turn before triggering inference."""
    from brain.frames import InAppMessageFrame, MessageType

    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "go",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("go")
    demo_id = h.processor._active_demonstration.id
    n_calls_before = len(h.llm.structured_views)

    fake_frame = InAppMessageFrame(
        message="",
        message_type=MessageType.TOOL_BATCH_COMPLETED,
        demonstration_id=demo_id,
        data={"batch_id": "ghost-batch-id-not-current", "size": 1},
    )
    await h.processor._handle_agent_turn(fake_frame)
    # No new inference fired.
    assert len(h.llm.structured_views) == n_calls_before


async def test_stale_tool_batch_completed_with_no_in_flight_dropped(harness_with_tools):
    from brain.frames import InAppMessageFrame, MessageType

    h = harness_with_tools
    n_calls_before = len(h.llm.structured_views)
    fake_frame = InAppMessageFrame(
        message="",
        message_type=MessageType.TOOL_BATCH_COMPLETED,
        demonstration_id=None,
        data={"batch_id": "ghost", "size": 1},
    )
    await h.processor._handle_agent_turn(fake_frame)
    assert len(h.llm.structured_views) == n_calls_before
