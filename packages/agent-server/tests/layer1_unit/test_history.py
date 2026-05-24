"""InAppConversationHistory unit tests: append, summarization swap,
shutdown, message-for-inference shape."""

from __future__ import annotations

import asyncio

from brain.conversation_history import InAppConversationHistory
from tests.harness.fakes import FakeSummaryChain


def _new_history(*, max_window: int = 30, summarize_chunk: int = 16):
    h = InAppConversationHistory(
        system_message="SYS",
        max_window=max_window,
        summarize_chunk=summarize_chunk,
    )
    fake = FakeSummaryChain()
    h._summary_chain = fake
    return h, fake


def test_messages_for_inference_starts_with_system_message_and_no_summary():
    h, _ = _new_history()
    h.append_user("hi")
    msgs = h.get_messages_for_inference()
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1] == {"role": "user", "content": "hi"}


def test_summary_renders_after_system_when_present():
    h, _ = _new_history()
    h._summary = "EARLIER"
    h.append_user("now")
    msgs = h.get_messages_for_inference()
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == "SYS"
    assert msgs[1]["role"] == "system" and "EARLIER" in msgs[1]["content"]


async def test_summary_kicks_only_after_max_window():
    h, fake = _new_history(max_window=5, summarize_chunk=3)
    fake.next_summary = "[summary]"
    for i in range(5):
        h.append_user(f"msg-{i}")
    assert fake.invoke_count == 0  # at threshold, not over
    h.append_user("msg-5")  # now over the window — kicks summary
    await fake.wait_for_invoke(timeout=1.0)
    # Wait for the swap to land.
    for _ in range(20):
        if h.has_summary:
            break
        await asyncio.sleep(0.01)
    assert h.has_summary
    # Tail should have lost the first summarize_chunk (3) entries.
    assert h.message_count == 6 - 3


async def test_only_one_summary_task_in_flight_at_a_time():
    h, fake = _new_history(max_window=5, summarize_chunk=3)
    # Block the first invocation so we can check no second one races.
    block_event = asyncio.Event()

    async def slow_invoke(payload):
        await block_event.wait()
        return FakeSummaryChain._Result("[s]")

    fake.ainvoke = slow_invoke  # type: ignore[assignment]

    for i in range(10):
        h.append_user(f"m-{i}")
    # Even though we appended way past window, only one summary task spawned.
    assert h._summary_task is not None
    # Yield so the task starts.
    await asyncio.sleep(0)
    # Append more — must NOT spawn a second task.
    first_task = h._summary_task
    for i in range(5):
        h.append_user(f"more-{i}")
    assert h._summary_task is first_task

    # Unblock and let it finish.
    block_event.set()
    await asyncio.sleep(0.05)
    await h.shutdown()


async def test_existing_summary_extended_not_stacked():
    h, fake = _new_history(max_window=5, summarize_chunk=3)
    h._summary = "OLD"
    fake.next_summary = "OLD + NEW (combined)"
    for i in range(6):
        h.append_user(f"m-{i}")
    await fake.wait_for_invoke(timeout=1.0)
    for _ in range(20):
        if "NEW" in (h._summary or ""):
            break
        await asyncio.sleep(0.01)
    assert h._summary == "OLD + NEW (combined)"
    # Verify the chain saw the previous summary in its payload.
    assert any("OLD" in c.get("previous_summary", "") for c in fake.calls)


async def test_summary_failure_leaves_log_intact():
    h, fake = _new_history(max_window=5, summarize_chunk=3)
    fake.raises = RuntimeError("gemini down")
    for i in range(6):
        h.append_user(f"m-{i}")
    await fake.wait_for_invoke(timeout=1.0)
    # Give the task time to finish handling the exception.
    for _ in range(20):
        if h._summary_task is None or h._summary_task.done():
            break
        await asyncio.sleep(0.01)
    assert not h.has_summary
    assert h.message_count == 6


async def test_shutdown_cancels_inflight_summary():
    h, fake = _new_history(max_window=5, summarize_chunk=3)

    async def hang(payload):
        await asyncio.sleep(60)
        return FakeSummaryChain._Result("never")

    fake.ainvoke = hang  # type: ignore[assignment]
    for i in range(6):
        h.append_user(f"m-{i}")
    await asyncio.sleep(0)
    assert h._summary_task is not None and not h._summary_task.done()
    await h.shutdown()
    assert h._summary_task is None or h._summary_task.done()


def test_append_assistant_tool_calls_shape():
    h, _ = _new_history()
    h.append_assistant_tool_calls(
        content="ok",
        tool_calls=[{"id": "x1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
    )
    msgs = h.get_messages_for_inference()
    last = msgs[-1]
    assert last["role"] == "assistant"
    assert last["content"] == "ok"
    assert last["tool_calls"][0]["id"] == "x1"


def test_append_tool_result_shape():
    h, _ = _new_history()
    h.append_tool_result(tool_call_id="x1", content='{"result":1}')
    msgs = h.get_messages_for_inference()
    last = msgs[-1]
    assert last["role"] == "tool"
    assert last["tool_call_id"] == "x1"
    assert last["content"] == '{"result":1}'


def test_chunk_must_be_smaller_than_window():
    import pytest

    with pytest.raises(ValueError):
        InAppConversationHistory(
            system_message="s", max_window=5, summarize_chunk=5,
        )
