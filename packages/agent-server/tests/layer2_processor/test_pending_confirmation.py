"""Pending-confirmation resolution machine: accept / replace / keep_waiting.
There is NO 'decline' option (would leave demo stuck)."""

from __future__ import annotations


async def test_propose_batch_with_confirmable_parks_it(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "I'll delete it — okay?",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            }
        ]
    )
    await h.send_user("delete task p1")
    h.assert_pending(confirmable=["delete_task"])
    # No dispatch yet — batch is parked.
    assert sum(1 for k, _ in h.batch_events if k == "dispatch") == 0
    h.assert_no_in_flight()
    h.assert_active_demo(name="delete", batches_dispatched=0)


async def test_accept_dispatches_held_batch_with_same_id(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "I'll delete it — okay?",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "Doing it.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "pending_batch_resolution": "accept",
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("delete p1")
    pending_batch_id = h.processor._pending_confirmation_batch.batch_id

    await h.send_user("yes do it")
    # Held batch dispatched; pending cleared; in-flight has the same batch_id.
    h.assert_no_pending()
    h.assert_in_flight(expected_size=1)
    assert h.processor._in_flight_batch.batch_id == pending_batch_id
    h.assert_active_demo(batches_dispatched=1)


async def test_replace_with_new_confirmable_reparks(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Delete? confirm please.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "Got it — instead I'll email the owner. ok?",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "pending_batch_resolution": "replace",
                "tool_invocations": [{"name": "send_email", "arguments": {"to": "owner"}}],
            },
        ]
    )
    await h.send_user("delete p1")
    old_batch_id = h.processor._pending_confirmation_batch.batch_id

    await h.send_user("no, email them instead")
    # New batch parked (still confirmable), no dispatch yet, batch_id changed.
    h.assert_pending(confirmable=["send_email"])
    assert h.processor._pending_confirmation_batch.batch_id != old_batch_id
    h.assert_no_in_flight()


async def test_replace_with_non_confirmable_dispatches_immediately(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "Listing instead.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "pending_batch_resolution": "replace",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
        ]
    )
    await h.send_user("delete p1")
    await h.send_user("no, just show me the list")
    h.assert_no_pending()
    h.assert_in_flight(expected_size=1)
    assert h.last_call_id(name="list_tasks") is not None


async def test_replace_with_empty_tool_calls_falls_through_to_keep_waiting(
    harness_with_tools,
):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {}}],
            },
            # LLM mis-uses replace with empty tool_calls.
            {
                "user_turn_status": "complete",
                "speech": "hmm.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "pending_batch_resolution": "replace",
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("delete it")
    pending_id = h.processor._pending_confirmation_batch.batch_id
    await h.send_user("uhh")
    # Batch is still parked under the SAME id.
    h.assert_pending()
    assert h.processor._pending_confirmation_batch.batch_id == pending_id


async def test_keep_waiting_speech_only_no_state_change(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "Sure, my name is AeliosSpark.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "pending_batch_resolution": "keep_waiting",
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("delete it")
    pending_id = h.processor._pending_confirmation_batch.batch_id
    await h.send_user("btw whats your name?")
    h.assert_pending()
    assert h.processor._pending_confirmation_batch.batch_id == pending_id
    h.assert_no_in_flight()
    h.assert_active_demo(batches_dispatched=0)


async def test_pending_plus_start_new_drops_pending(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "switching to listing.",
                "demonstration_action": "start_new",
                "demonstration_name": "listing",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
        ]
    )
    await h.send_user("delete it")
    h.assert_pending()
    await h.send_user("never mind, just list them")
    h.assert_no_pending()
    h.assert_active_demo(name="listing", batches_dispatched=1)
    h.assert_in_flight(expected_size=1)


async def test_pending_plus_end_current_drops_pending(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "ok cancelling.",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("delete it")
    await h.send_user("never mind")
    h.assert_no_pending()
    h.assert_no_active_demo()


async def test_pending_continue_with_invocations_and_no_resolution_discards(
    harness_with_tools,
):
    """Corrupt-state guard, Case A.

    Pending batch parked. LLM emits demonstration_action='continue' AND
    populated tool_invocations BUT omits pending_batch_resolution
    (schema should prevent this, but we defend in depth). The processor
    must NOT dispatch the new tool_invocations alongside the parked
    batch — that would corrupt the mutually-exclusive batch state. The
    two-trigger-rule guard discards the spurious tool_invocations and
    leaves the pending batch parked.
    """
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {}}],
            },
            # Corrupt-looking output: continue + new tool_invocations
            # but no pending_batch_resolution. This is what we guard
            # against — must NOT dispatch.
            {
                "user_turn_status": "complete",
                "speech": "while you think, here's the list",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
                # pending_batch_resolution intentionally omitted.
            },
        ]
    )
    await h.send_user("delete it")
    pending_id = h.processor._pending_confirmation_batch.batch_id
    dispatch_count_before = sum(1 for k, _ in h.batch_events if k == "dispatch")

    await h.send_user("ok thinking")

    # Pending batch is unchanged — same batch_id, still parked.
    h.assert_pending()
    assert h.processor._pending_confirmation_batch.batch_id == pending_id
    h.assert_no_in_flight()
    # No new dispatch happened. The spurious tool_invocations were
    # discarded by the two-trigger-rule guard.
    dispatch_count_after = sum(1 for k, _ in h.batch_events if k == "dispatch")
    assert dispatch_count_after == dispatch_count_before
    h.assert_active_demo(batches_dispatched=0)


async def test_keep_waiting_with_populated_invocations_silently_drops_them(
    harness_with_tools,
):
    """Corrupt-state guard, Case C.

    Pending batch parked. LLM emits keep_waiting AND populated
    tool_invocations. _resolve_pending_batch's keep_waiting branch is
    speech-only by design — replacement_invocations are silently
    ignored. Pending batch stays parked.
    """
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "still here, take your time",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "pending_batch_resolution": "keep_waiting",
                # Spurious — must be ignored.
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
        ]
    )
    await h.send_user("delete it")
    pending_id = h.processor._pending_confirmation_batch.batch_id
    dispatch_count_before = sum(1 for k, _ in h.batch_events if k == "dispatch")

    await h.send_user("hmm")

    h.assert_pending()
    assert h.processor._pending_confirmation_batch.batch_id == pending_id
    h.assert_no_in_flight()
    dispatch_count_after = sum(1 for k, _ in h.batch_events if k == "dispatch")
    assert dispatch_count_after == dispatch_count_before
    h.assert_active_demo(batches_dispatched=0)


async def test_accept_with_populated_invocations_dispatches_parked_not_new(
    harness_with_tools,
):
    """Corrupt-state guard, Case B.

    Pending batch parked. LLM emits accept AND populated tool_invocations
    (which would be a new batch). The accept path dispatches the PARKED
    batch and silently drops the spurious replacement_invocations —
    only one batch becomes in-flight, and it's the parked one.
    """
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "doing it.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "pending_batch_resolution": "accept",
                # Spurious — must be ignored; parked delete_task
                # is what should dispatch.
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
        ]
    )
    await h.send_user("delete p1")
    parked_batch_id = h.processor._pending_confirmation_batch.batch_id

    await h.send_user("yes do it")

    h.assert_no_pending()
    h.assert_in_flight(expected_size=1)
    # The dispatched batch is the originally-parked one, not a new one.
    assert h.processor._in_flight_batch.batch_id == parked_batch_id
    # The dispatched call is delete_task (parked), not
    # list_tasks (the spurious replacement).
    assert h.last_call_id(name="delete_task") is not None
    assert h.last_call_id(name="list_tasks") is None
    h.assert_active_demo(batches_dispatched=1)


async def test_state_context_in_pending_lists_proposed_batch(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            }
        ]
    )
    await h.send_user("delete p1")
    msg = h.processor._build_state_context_message()
    assert "Batch pending confirmation" in msg
    assert "delete_task" in msg
    assert "REQUIRES CONFIRMATION" in msg
    assert "no plain" in msg.lower() and "decline" in msg.lower()
