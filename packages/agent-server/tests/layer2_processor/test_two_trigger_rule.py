"""Two-trigger rule: tool_calls allowed only on
  (a) user input wake + start_new, OR
  (b) tool_batch_completed wake, OR
  (c) pending_confirmation + replace.
Everything else is discarded."""

from __future__ import annotations


async def test_tool_calls_under_continue_idle_are_discarded(harness_with_tools):
    h = harness_with_tools
    # No demo, idle, user wake, but LLM returns demonstration_action=continue
    # with non-empty tool_calls — schema should not have allowed it but if
    # it slipped through, the processor must drop them.
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "ok",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("show me")
    # No dispatch happened; no demo started.
    assert sum(1 for k, _ in h.batch_events if k == "dispatch") == 0
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_tool_calls_under_end_current_discarded(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            # Start demo
            {
                "user_turn_status": "complete",
                "speech": "ok",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            # User asks to stop; LLM returns end_current + tool_calls.
            {
                "user_turn_status": "complete",
                "speech": "stopping",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [{"name": "create_task", "arguments": {"name": "X"}}],
            },
        ]
    )
    await h.send_user("go")
    n_before = sum(1 for k, _ in h.batch_events if k == "dispatch")

    await h.send_user("stop")
    n_after = sum(1 for k, _ in h.batch_events if k == "dispatch")
    assert n_after == n_before  # no dispatch under end_current
    h.assert_no_active_demo()


async def test_tool_calls_allowed_on_tool_batch_completed_wake(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "starting",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            {
                "speech": "next step",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [{"name": "get_status", "arguments": {}}],
            },
        ]
    )
    await h.send_user("go")
    cid = h.last_call_id(name="list_tasks")
    await h.deliver_tool_result(call_id=cid, result="ok")
    # Second dispatch under same demo allowed via tool_batch_completed wake.
    assert sum(1 for k, _ in h.batch_events if k == "dispatch") == 2


# The positive path (user wake + start_new with tools → dispatch) is
# covered by test_demo_lifecycle.test_first_user_request_starts_demo_
# and_dispatches_batch. Schema-shape-when-no-tools is covered by
# layer1_unit/test_schema.test_tool_calls_hidden_when_no_tools_registered.
# Both were duplicated here previously — removed to keep this file
# focused on the *guard* behaviour the two-trigger rule actually owns.
