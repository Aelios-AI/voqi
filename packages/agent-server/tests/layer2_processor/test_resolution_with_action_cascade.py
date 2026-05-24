"""When the LLM produces ``demonstration_action`` of ``start_new`` or
``end_current`` alongside a ``pending_batch_resolution`` value, the
resolution must be a no-op — the cancellation cascade has already
dropped the pending batch, so there is nothing left for
``_resolve_pending_batch`` to act on."""

from __future__ import annotations


async def test_start_new_alongside_accept_ignores_resolution(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            # Round 1: propose a confirmable batch (delete_task requires
            # confirmation) → server parks it pending.
            {
                "user_turn_status": "complete",
                "speech": "Delete? confirm please.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete-x",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            },
            # Round 2: visitor changes mind. LLM emits start_new + accept
            # AND a fresh tool batch under the new demo. The accept must
            # NOT cause the old (parked) delete_task batch to dispatch.
            {
                "user_turn_status": "complete",
                "speech": "Switching to listing instead.",
                "demonstration_action": "start_new",
                "demonstration_name": "list-them",
                "pending_batch_resolution": "accept",  # ← should be ignored
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
        ]
    )
    await h.send_user("delete task p1")
    h.assert_pending(confirmable=["delete_task"])
    pending_batch_id = h.processor._pending_confirmation_batch.batch_id

    await h.send_user("never mind, just list them")

    # The old pending batch did NOT dispatch (cascade dropped it before
    # the resolution check ran).
    dispatched_names = [
        tc["name"]
        for kind, payload in h.batch_events
        if kind == "dispatch"
        for tc in payload["tool_calls"]
    ]
    assert "delete_task" not in dispatched_names
    # Only the NEW batch under the new demo dispatched.
    assert dispatched_names == ["list_tasks"]
    h.assert_active_demo(name="list-them", batches_dispatched=1)
    h.assert_no_pending()
    # Sanity: the in-flight batch is the NEW one, not the old pending.
    assert h.processor._in_flight_batch.batch_id != pending_batch_id


async def test_end_current_alongside_accept_ignores_resolution(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Delete? confirm please.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete-x",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            },
            # Visitor declines. LLM (incorrectly) chose accept while also
            # ending the demo — accept must be ignored, no dispatch.
            {
                "user_turn_status": "complete",
                "speech": "Okay, cancelling.",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "pending_batch_resolution": "accept",
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("delete task p1")
    await h.send_user("nope, stop")

    # No batch ever dispatched (the proposal was parked, then the cascade
    # dropped it before resolution).
    dispatched = [k for k, _ in h.batch_events if k == "dispatch"]
    assert dispatched == []
    h.assert_no_active_demo()
    h.assert_no_pending()
    h.assert_no_in_flight()


async def test_end_current_alongside_replace_ignores_resolution(harness_with_tools):
    """A ``replace`` paired with ``end_current`` is also a no-op — the
    cascade drops the pending batch, and end_current's contract says no
    new tool batch dispatches under it (tool_invocations are discarded
    in _run_one_round under end_current)."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Delete? confirm please.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete-x",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "Stopping entirely.",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "pending_batch_resolution": "replace",
                # Even though tool_invocations is non-empty, end_current
                # discards them.
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
        ]
    )
    await h.send_user("delete it")
    await h.send_user("never mind, stop")
    dispatched = [k for k, _ in h.batch_events if k == "dispatch"]
    assert dispatched == []
    h.assert_no_active_demo()
    h.assert_no_pending()


async def test_start_new_with_keep_waiting_is_correct_no_op(harness_with_tools):
    """The master prompt tells the LLM to set ``keep_waiting`` when
    pairing the resolution with ``start_new``/``end_current``. Confirm
    that combination behaves identically to the action-alone path."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete-x",
                "tool_invocations": [{"name": "delete_task", "arguments": {}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "Switching to listing.",
                "demonstration_action": "start_new",
                "demonstration_name": "list",
                "pending_batch_resolution": "keep_waiting",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
        ]
    )
    await h.send_user("delete it")
    await h.send_user("nope, list them")
    # Old pending dropped, new demo + new batch dispatched.
    h.assert_no_pending()
    h.assert_active_demo(name="list", batches_dispatched=1)
    h.assert_in_flight(expected_size=1)
    assert h.last_call_id(name="list_tasks") is not None
