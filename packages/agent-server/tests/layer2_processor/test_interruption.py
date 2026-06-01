"""Interruption handling.

  - Voice barge-in does NOT cancel pending tool tasks.
  - Queue is drained except put_back items.
  - send-text-message interrupts and routes to TEXT_MESSAGE.
  - Active incomplete-timeout is cancelled.
"""

from __future__ import annotations

import asyncio

from brain.frames import (
    InAppMessageFrame,
    MessageType,
    RankedEnvelope,
)


async def test_interruption_does_not_cancel_in_flight_batch(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "running",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("go")
    h.assert_in_flight()
    demo_id = h.processor._active_demonstration.id

    await h.processor._handle_interruption()
    # Demo + in-flight batch survive interruption.
    h.assert_active_demo()
    assert h.processor._active_demonstration.id == demo_id
    h.assert_in_flight()


async def test_interruption_keeps_put_back_tool_batches(harness_with_tools):
    """Interruption drains the queue but keeps frames flagged with
    ``put_back_when_interrupted=True``. Tool-batch-completed frames
    use this so a mid-conversation interruption doesn't lose a tool
    result the next round still needs."""
    h = harness_with_tools
    h.processor._enqueue(
        priority=3,
        frame=InAppMessageFrame(
            message="",
            message_type=MessageType.TOOL_BATCH_COMPLETED,
            put_back_when_interrupted=True,
            demonstration_id="demo-z",
            data={"batch_id": "b1", "size": 1},
        ),
    )
    # An ephemeral frame (put_back=False) gets dropped.
    h.processor._enqueue(
        priority=2,
        frame=InAppMessageFrame(
            message="ephemeral",
            message_type=MessageType.CANNED_SPEECH,
            put_back_when_interrupted=False,
        ),
    )
    await h.processor._handle_interruption()
    types = []
    while not h.processor._wake_queue.empty():
        item: RankedEnvelope = h.processor._wake_queue.get_nowait()
        types.append(item.frame.message_type)
    assert MessageType.TOOL_BATCH_COMPLETED in types
    assert MessageType.CANNED_SPEECH not in types


async def test_send_text_message_interrupts_and_enqueues_text(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "speech": "got your typed input",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            }
        ]
    )
    # Pre-load a stale put_back=False frame. The text-message
    # interruption must drop it before processing the new text wake.
    ghost = InAppMessageFrame(
        message="",
        message_type=MessageType.TOOL_BATCH_COMPLETED,
        put_back_when_interrupted=False,
        demonstration_id="ghost",
        data={"batch_id": "ghost-b", "size": 0},
    )
    h.processor._enqueue(priority=3, frame=ghost)

    await h.send_text_message("hello there")

    # The new text turn produced the scripted reply.
    last = h.assistant_speech_history[-1]
    assert last["role"] == "assistant"
    assert "got your typed input" in last["content"]
    # The pre-loaded stale frame was dropped by the interruption — it
    # never made it onto the pump path. Drain whatever's currently on
    # the queue and verify the ghost is not among the remaining items.
    remaining_frames = []
    while not h.processor._wake_queue.empty():
        remaining_frames.append(h.processor._wake_queue.get_nowait().frame)
    assert ghost not in remaining_frames, (
        "stale put_back=False TOOL_BATCH_COMPLETED frame should have been "
        "dropped by send_text_message's interruption, but it survived"
    )


async def test_processing_resumes_after_interruption(harness_with_tools):
    h = harness_with_tools
    await h.processor._handle_interruption()
    # _processing_blocked must be False after interruption so the next
    # message can be processed.
    assert h.processor._processing_blocked is False
