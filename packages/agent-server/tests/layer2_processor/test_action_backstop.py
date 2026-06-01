"""Defensive backstop in `_run_one_round`: if the LLM emits a
`demonstration_action` value that isn't in the per-wake enum (model
bug or schema drift), coerce to 'continue' so we don't start/end a
demo on a wake where that's nonsensical."""

from __future__ import annotations


async def test_start_new_on_tool_batch_completed_is_coerced(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            # Round 1 — start a demo with a batch.
            {
                "user_turn_status": "complete",
                "speech": "go",
                "demonstration_action": "start_new",
                "demonstration_name": "first",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            # Round 2 fires on the tool_batch_completed wake. The
            # scripted output (illegally) carries start_new — the
            # schema would never let this happen in production but we
            # script it here to verify the backstop.
            {
                "speech": "switching!",
                "demonstration_action": "start_new",
                "demonstration_name": "should-not-happen",
                "tool_invocations": [{"name": "create_task", "arguments": {"name": "X"}}],
            },
        ]
    )
    await h.send_user("show me")
    cid = h.last_call_id(name="list_tasks")
    await h.deliver_tool_result(call_id=cid, result="ok")
    # Backstop coerced action='start_new' to 'continue'. The original
    # demo survives and no new demo named 'should-not-happen' was
    # started.
    h.assert_active_demo(name="first")
    starts = [e for e in h.demo_events if e[0] == "start_new"]
    assert [name for _, _, name in starts] == ["first"]


# The positive-path "valid action passes through" assertion is covered
# by test_demo_lifecycle.test_first_user_request_starts_demo_and_
# dispatches_batch (which makes the same assertion as a side effect of
# the lifecycle scenario it's actually testing).
