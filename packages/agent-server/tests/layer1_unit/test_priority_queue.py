"""RankedEnvelope ordering: USER_MESSAGE preempts TOOL_BATCH_COMPLETED;
SYSTEM recovery (priority 0) preempts user; FIFO within a tier."""

from __future__ import annotations

import asyncio

from brain.frames import (
    InAppMessageFrame,
    MessageType,
    RankedEnvelope,
)


def _frame(mt: MessageType, msg: str = "") -> InAppMessageFrame:
    return InAppMessageFrame(message=msg, message_type=mt)


async def test_user_message_preempts_tool_batch_completed():
    q: asyncio.PriorityQueue[RankedEnvelope] = asyncio.PriorityQueue()
    q.put_nowait(
        RankedEnvelope(priority=3, frame=_frame(MessageType.TOOL_BATCH_COMPLETED, "tool"))
    )
    q.put_nowait(RankedEnvelope(priority=1, frame=_frame(MessageType.USER_MESSAGE, "user")))
    first = await q.get()
    assert first.frame.message_type == MessageType.USER_MESSAGE


async def test_system_recovery_preempts_user_message():
    q: asyncio.PriorityQueue[RankedEnvelope] = asyncio.PriorityQueue()
    q.put_nowait(RankedEnvelope(priority=1, frame=_frame(MessageType.USER_MESSAGE, "user")))
    q.put_nowait(RankedEnvelope(priority=0, frame=_frame(MessageType.CANNED_SPEECH, "recovery")))
    first = await q.get()
    assert first.frame.message_type == MessageType.CANNED_SPEECH


async def test_fifo_within_same_priority():
    q: asyncio.PriorityQueue[RankedEnvelope] = asyncio.PriorityQueue()
    a = RankedEnvelope(priority=1, frame=_frame(MessageType.USER_MESSAGE, "first"))
    b = RankedEnvelope(priority=1, frame=_frame(MessageType.USER_MESSAGE, "second"))
    q.put_nowait(a)
    q.put_nowait(b)
    first = await q.get()
    second = await q.get()
    assert first.frame.message == "first"
    assert second.frame.message == "second"


async def test_incomplete_nudge_priority_2_between_user_and_tools():
    q: asyncio.PriorityQueue[RankedEnvelope] = asyncio.PriorityQueue()
    q.put_nowait(RankedEnvelope(priority=3, frame=_frame(MessageType.TOOL_BATCH_COMPLETED, "t")))
    q.put_nowait(RankedEnvelope(priority=2, frame=_frame(MessageType.CANNED_SPEECH, "nudge")))
    q.put_nowait(RankedEnvelope(priority=1, frame=_frame(MessageType.USER_MESSAGE, "u")))
    order = []
    while not q.empty():
        order.append((await q.get()).frame.message_type)
    assert order == [
        MessageType.USER_MESSAGE,
        MessageType.CANNED_SPEECH,
        MessageType.TOOL_BATCH_COMPLETED,
    ]
