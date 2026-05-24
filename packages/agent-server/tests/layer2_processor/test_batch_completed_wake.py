"""TOOL_BATCH_COMPLETED wake reason names the tools that just finished
so the LLM doesn't have to chase call_ids back through history."""

from __future__ import annotations


async def test_wake_reason_lists_tool_names(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "starting",
                "demonstration_action": "start_new",
                "demonstration_name": "demo",
                "tool_invocations": [
                    {"name": "list_tasks", "arguments": {}},
                    {"name": "get_status", "arguments": {}},
                ],
            }
        ]
    )
    await h.send_user("show me")
    h.assert_in_flight(expected_size=2)
    # Build the wake reason for the (yet-to-fire) TOOL_BATCH_COMPLETED.
    from brain.frames import InAppMessageFrame, MessageType

    frame = InAppMessageFrame(
        message="",
        message_type=MessageType.TOOL_BATCH_COMPLETED,
        demonstration_id=h.processor._active_demonstration.id,
        data={"batch_id": h.processor._in_flight_batch.batch_id, "size": 2},
    )
    reason = h.processor._wake_reason_for(frame)
    assert "list_tasks" in reason
    assert "get_status" in reason
    assert "resolved" in reason or "results" in reason or "progress" in reason


async def test_in_flight_batch_records_tool_names(harness_with_tools):
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
                    {"name": "create_task", "arguments": {"name": "P"}},
                ],
            }
        ]
    )
    await h.send_user("go")
    ifb = h.processor._in_flight_batch
    assert ifb is not None
    assert ifb.tool_names == ["list_tasks", "create_task"]
