"""Demonstration lifecycle: continue / start_new / end_current.

Scenarios 1-6 from the test plan. Covers:
  - Kickoff with no demo
  - First user request → start_new + dispatch
  - Tool batch resolves → next batch under same demo
  - Demo goal reached → end_current
  - end_current with stray tool_calls (discarded)
  - start_new mid-demo cancels prior
"""

from __future__ import annotations


async def test_kickoff_no_demo_started(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "speech": "Hi, how can I help?",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            }
        ]
    )
    await h.send_kickoff()
    h.assert_no_active_demo()
    h.assert_no_in_flight()
    assert ("start_new", *("",) * 2) not in h.demo_events
    # No tool_call_batch wire message sent.
    assert h.rtvi.server_messages_of_type("tool_call_batch") == []


async def test_first_user_request_starts_demo_and_dispatches_batch(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "Sure, listing tasks for you.",
                "demonstration_action": "start_new",
                "demonstration_name": "list tasks",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("show me my tasks")
    h.assert_active_demo(name="list tasks", batches_dispatched=1)
    h.assert_in_flight(expected_size=1)
    batch_msg = h.last_dispatched_batch()
    assert batch_msg is not None
    assert batch_msg["type"] == "tool_call_batch"
    assert batch_msg["tool_calls"][0]["name"] == "list_tasks"
    assert batch_msg["tool_calls"][0]["args"] == {}


async def test_tool_batch_resolves_then_next_batch_under_same_demo(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            # Round 1 — start demo, fire batch [list_tasks]
            {
                "user_turn_status": "complete",
                "speech": "Listing them now.",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            # Round 2 — TOOL_BATCH_COMPLETED wake — fire batch [get_status]
            {
                "speech": "Now checking status.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [{"name": "get_status", "arguments": {}}],
            },
            # Round 3 — TOOL_BATCH_COMPLETED wake — wrap up
            {
                "speech": "All done.",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("show me everything")
    h.assert_in_flight()
    first_demo_id = h.processor._active_demonstration.id
    first_call_id = h.last_call_id(name="list_tasks")
    assert first_call_id is not None

    await h.deliver_tool_result(call_id=first_call_id, result=["a", "b"])

    # Round 2 dispatched a new batch under same demo
    h.assert_active_demo(name="browse", batches_dispatched=2)
    assert h.processor._active_demonstration.id == first_demo_id
    h.assert_in_flight()
    second_call_id = h.last_call_id(name="get_status")
    assert second_call_id is not None and second_call_id != first_call_id

    await h.deliver_tool_result(call_id=second_call_id, result={"ok": True})

    # Round 3 ended the demo cleanly
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_end_current_with_stray_tool_calls_drops_them(harness_with_tools):
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
            # User asks to wrap up — LLM (incorrectly) returns end_current
            # along with tool_calls. Processor MUST drop them.
            {
                "user_turn_status": "complete",
                "speech": "Stopping there.",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [{"name": "create_task", "arguments": {"name": "X"}}],
            },
        ]
    )
    await h.send_user("kick it off")
    h.assert_in_flight()
    n_dispatches_before = sum(1 for k, _ in h.batch_events if k == "dispatch")

    await h.send_user("never mind, stop")
    n_dispatches_after = sum(1 for k, _ in h.batch_events if k == "dispatch")
    # No NEW dispatch happened — the stray tool_calls were discarded.
    assert n_dispatches_after == n_dispatches_before
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_start_new_mid_demo_cancels_prior(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            # Round 1 — start demo "browse" with batch
            {
                "user_turn_status": "complete",
                "speech": "Browsing.",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            # Round 2 (user wake) — start_new "create" with new batch
            {
                "user_turn_status": "complete",
                "speech": "Switching to create.",
                "demonstration_action": "start_new",
                "demonstration_name": "create",
                "tool_invocations": [{"name": "create_task", "arguments": {"name": "P"}}],
            },
        ]
    )
    await h.send_user("browse")
    first_demo_id = h.processor._active_demonstration.id
    first_call_id = h.last_call_id(name="list_tasks")

    await h.send_user("actually, create one called P")

    # New demo replaces old one; new id; old in-flight batch cancelled.
    h.assert_active_demo(name="create", batches_dispatched=1)
    assert h.processor._active_demonstration.id != first_demo_id
    h.assert_in_flight()
    new_call_id = h.last_call_id(name="create_task")
    assert new_call_id != first_call_id

    # Two start_new events recorded.
    starts = [e for e in h.demo_events if e[0] == "start_new"]
    assert len(starts) == 2
    assert [name for _, _, name in starts] == ["browse", "create"]


# The "no-demo state context says 'No active demonstration'" sanity is
# covered by test_state_context_sections.test_no_demo_no_history_block,
# which checks the same render path plus the absence of the batch-
# history section.
