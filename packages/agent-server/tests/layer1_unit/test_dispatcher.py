"""ToolDispatcher unit tests: register, deliver, cancel, timeout, late-result."""

from __future__ import annotations

import asyncio

import pytest

from brain.tool_dispatcher import (
    ToolDispatcher,
    ToolDispatchOutcome,
)


class _OutcomeRecorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def __call__(
        self,
        demo_id: str,
        batch_id: str,
        call_id: str,
        name: str,
        args: dict,
        outcome: str,
        payload: object,
    ) -> None:
        self.events.append((demo_id, batch_id, call_id, name, args, outcome, payload))


async def test_dispatch_batch_registers_per_call_tasks():
    rec = _OutcomeRecorder()
    d = ToolDispatcher(on_outcome=rec)
    demo, batch = "demo-A", "batch-A"
    await d.dispatch_batch(
        demo_id=demo,
        batch_id=batch,
        tool_calls=[
            {"id": "c1", "name": "t1", "arguments": "{}"},
            {"id": "c2", "name": "t2", "arguments": "{}"},
        ],
    )
    assert len(d._tasks[demo]) == 2
    assert d._call_batch["c1"] == batch
    assert d._call_batch["c2"] == batch
    assert d.tool_call_count == 2

    # Yield so each per-call task runs far enough to register its future.
    await asyncio.sleep(0)
    d.deliver_widget_result(call_id="c1", data={"result": {"ok": True}})
    d.deliver_widget_result(call_id="c2", data={"result": 42})
    await asyncio.gather(*list(d._tasks.get(demo, {}).values()), return_exceptions=True)
    await d.shutdown()
    outcomes = sorted([(call_id, outcome) for _, _, call_id, _, _, outcome, _ in rec.events])
    assert outcomes == [("c1", ToolDispatchOutcome.SUCCESS), ("c2", ToolDispatchOutcome.SUCCESS)]


async def test_widget_result_with_error_marks_failed():
    rec = _OutcomeRecorder()
    d = ToolDispatcher(on_outcome=rec)
    await d.dispatch_batch(
        demo_id="d",
        batch_id="b",
        tool_calls=[{"id": "c1", "name": "t", "arguments": "{}"}],
    )
    await asyncio.sleep(0)
    d.deliver_widget_result(call_id="c1", data={"error": "boom"})
    await asyncio.gather(*list(d._tasks.get("d", {}).values()), return_exceptions=True)
    await d.shutdown()
    assert rec.events[0][5] == ToolDispatchOutcome.FAILED
    assert rec.events[0][6] == "boom"


async def test_unknown_call_id_dropped_silently():
    rec = _OutcomeRecorder()
    d = ToolDispatcher(on_outcome=rec)
    # Just calling deliver_widget_result with an unknown call_id should not raise.
    d.deliver_widget_result(call_id="nope", data={"result": 1})
    await d.shutdown()
    assert rec.events == []


async def test_cancel_demonstration_cancels_pending_calls_no_callback():
    rec = _OutcomeRecorder()
    d = ToolDispatcher(on_outcome=rec)
    await d.dispatch_batch(
        demo_id="d",
        batch_id="b",
        tool_calls=[{"id": "c1", "name": "t", "arguments": "{}"}],
    )
    await d.cancel_demonstration("d")
    # Outcome callback MUST NOT fire for CANCELLED.
    assert rec.events == []
    assert "d" not in d._tasks


async def test_cancel_unknown_demo_is_noop():
    rec = _OutcomeRecorder()
    d = ToolDispatcher(on_outcome=rec)
    await d.cancel_demonstration("ghost")
    await d.shutdown()


async def test_outcome_callback_raising_does_not_crash_dispatcher():
    async def bad_cb(*_, **__):
        raise RuntimeError("callback exploded")

    d = ToolDispatcher(on_outcome=bad_cb)
    await d.dispatch_batch(
        demo_id="d",
        batch_id="b",
        tool_calls=[{"id": "c1", "name": "t", "arguments": "{}"}],
    )
    await asyncio.sleep(0)
    d.deliver_widget_result(call_id="c1", data={"result": "ok"})
    await asyncio.gather(*list(d._tasks.get("d", {}).values()), return_exceptions=True)
    await d.shutdown()


async def test_dispatch_after_shutdown_is_noop():
    rec = _OutcomeRecorder()
    d = ToolDispatcher(on_outcome=rec)
    await d.shutdown()
    await d.dispatch_batch(
        demo_id="d",
        batch_id="b",
        tool_calls=[{"id": "c1", "name": "t", "arguments": "{}"}],
    )
    assert rec.events == []
    assert d._tasks == {} or all(not bucket for bucket in d._tasks.values())


def test_new_demonstration_id_and_batch_id_are_unique():
    ids = {ToolDispatcher.new_demonstration_id() for _ in range(50)}
    assert len(ids) == 50
    bids = {ToolDispatcher.new_batch_id() for _ in range(50)}
    assert len(bids) == 50
