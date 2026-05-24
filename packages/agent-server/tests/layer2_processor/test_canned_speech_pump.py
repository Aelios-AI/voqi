"""Canned speech goes through the same priority-queue → message-pump
→ handler → frame-push path as everything else. There is no separate
direct-push path: ``_speak_canned`` enqueues a CANNED_SPEECH frame at
priority 0 and the pump processes it on the next cycle."""

from __future__ import annotations

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)

from brain.canned_speech import CannedKey
from brain.frames import MessageType


async def test_speak_canned_enqueues_priority_zero_frame(harness_with_tools):
    h = harness_with_tools
    # Direct call (synchronous) — should land a CANNED_SPEECH frame on
    # the priority queue without pushing any frame yet.
    h.processor._speak_canned(CannedKey.LLM_GENERIC_ERROR)
    # Drain the queue; the topmost item should be CANNED_SPEECH at p0.
    item = h.processor._wake_queue.get_nowait()
    assert item.priority == 0
    assert item.frame.message_type == MessageType.CANNED_SPEECH
    assert item.frame.data.get("canned_key") == CannedKey.LLM_GENERIC_ERROR.value


async def test_canned_speech_pushed_only_after_pump_processes(harness_with_tools):
    h = harness_with_tools
    h.processor._speak_canned(CannedKey.LLM_GENERIC_ERROR)
    # Before pumping, no LLMText/Start/End frames have been pushed.
    assert not any(
        isinstance(f, (LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame))
        for f in h.pushed_frames
    )
    await h.pump_until_idle()
    # After the pump, the triple has landed and the transcript carries it.
    starts = [f for f in h.pushed_frames if isinstance(f, LLMFullResponseStartFrame)]
    ends = [f for f in h.pushed_frames if isinstance(f, LLMFullResponseEndFrame)]
    texts = [f for f in h.pushed_frames if isinstance(f, LLMTextFrame)]
    assert starts and ends and texts
    assert h.assistant_speech_history[-1]["role"] == "assistant"


async def test_canned_speech_preempts_pending_tool_batch_completed(harness_with_tools):
    """Priority 0 means the apology fires BEFORE any backlog of
    TOOL_BATCH_COMPLETED frames (priority 3). Verifies the ordering
    that the inference-retry path relies on."""
    h = harness_with_tools
    # Manually enqueue a TOOL_BATCH_COMPLETED frame and a canned speech
    # together; pop them in priority order.
    from brain.frames import InAppMessageFrame

    h.processor._enqueue(
        priority=3,
        frame=InAppMessageFrame(
            message="",
            message_type=MessageType.TOOL_BATCH_COMPLETED,
            put_back_when_interrupted=True,
            data={"batch_id": "x"},
        ),
    )
    h.processor._speak_canned(CannedKey.LLM_GENERIC_ERROR)
    first = h.processor._wake_queue.get_nowait()
    second = h.processor._wake_queue.get_nowait()
    assert first.frame.message_type == MessageType.CANNED_SPEECH
    assert second.frame.message_type == MessageType.TOOL_BATCH_COMPLETED


async def test_canned_speech_history_appended(harness_with_tools):
    h = harness_with_tools
    h.processor._speak_canned(CannedKey.RESPONSE_TIMEOUT)
    await h.pump_until_idle()
    # The canned text should be appended as a plain assistant message
    # in conversation history (no tool_calls).
    msgs = h.processor._history.get_messages_for_inference()
    last = msgs[-1]
    assert last["role"] == "assistant"
    assert last["content"]
    assert "tool_calls" not in last


async def test_canned_speech_is_only_visitor_facing_path(harness_with_tools):
    """Architectural property: the only way a canned utterance reaches
    the widget is through the message-pump path. ``_speak_canned`` is
    synchronous and never pushes frames itself — so directly calling
    it produces zero frames until the pump runs."""
    h = harness_with_tools
    n_frames_before = len(h.pushed_frames)
    h.processor._speak_canned(CannedKey.BATCH_CEILING_HIT)
    h.processor._speak_canned(CannedKey.RESPONSE_TIMEOUT)
    h.processor._speak_canned(CannedKey.LLM_GENERIC_ERROR)
    # Three calls, three queue entries, ZERO new pushed frames yet.
    assert len(h.pushed_frames) == n_frames_before
    assert h.processor._wake_queue.qsize() == 3
