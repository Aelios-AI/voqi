"""Cost-and-correctness guardrails: per-batch timeout, inference retry
cap, per-demo batch ceiling, response-timeout recovery."""

from __future__ import annotations

import asyncio


async def test_per_batch_timeout_force_completes_unresolved_calls(harness_with_tools):
    h = harness_with_tools
    h.set_timeouts(batch_seconds=0.1)
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "running",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [
                    {"name": "list_tasks", "arguments": {}},
                    {"name": "get_status", "arguments": {}},
                ],
            },
            # After force-complete a synthetic TOOL_BATCH_COMPLETED arrives
            # → next inference. LLM ends.
            {
                "speech": "Some calls timed out — wrapping up.",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("go")
    h.assert_in_flight(expected_size=2)
    # Don't deliver any tool results; let the batch timeout fire.
    await asyncio.sleep(0.2)
    await h.pump_until_idle()
    # Demo ended, no in-flight batch.
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_inference_retry_first_failures_re_enqueue_then_succeed(harness_with_tools):
    h = harness_with_tools
    h.set_inference_retry_limit(3)
    # Round 1: start demo, dispatch.
    # Round 2: tool_batch_completed inference RAISES.
    # Round 3: retry RAISES.
    # Round 4: retry SUCCEEDS — wraps up.
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "go",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    h.script_llm_error()
    h.script_llm_error()
    h.script_llm_outputs(
        [
            {
                "speech": "Recovered — done.",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [],
            }
        ]
    )
    await h.send_user("go")
    cid = h.last_call_id(name="list_tasks")
    await h.deliver_tool_result(call_id=cid, result="ok")
    # Two LLM_GENERIC_ERROR canned speeches expected for the two failed retries.
    from brain.canned_speech import CannedKey

    assert h.canned_spoken.count(CannedKey.LLM_GENERIC_ERROR) == 2
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_inference_retry_exhausted_force_ends_demo(harness_with_tools):
    h = harness_with_tools
    h.set_inference_retry_limit(2)
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "go",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    h.script_llm_error()
    h.script_llm_error()
    await h.send_user("go")
    cid = h.last_call_id(name="list_tasks")
    await h.deliver_tool_result(call_id=cid, result="ok")

    from brain.canned_speech import CannedKey

    assert CannedKey.INFERENCE_RETRY_EXHAUSTED in h.canned_spoken
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_inference_failure_on_user_wake_just_apologizes(harness_with_tools):
    h = harness_with_tools
    h.script_llm_error()
    await h.send_user("hello")
    from brain.canned_speech import CannedKey

    assert h.canned_spoken == [CannedKey.LLM_GENERIC_ERROR]
    # No demo started, no in-flight batch.
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_per_demo_batch_ceiling_force_ends_with_canned(harness_with_tools):
    h = harness_with_tools
    h.set_max_batches_per_demo(2)
    # Round 1: start demo + first batch (counter=1).
    # Round 2: continue + new batch (counter=2).
    # Round 3: continue + new batch (would be 3 → over ceiling → force-end).
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "1",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            {
                "speech": "2",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [{"name": "get_status", "arguments": {}}],
            },
            {
                "speech": "3",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [{"name": "get_status", "arguments": {}}],
            },
        ]
    )
    await h.send_user("go")
    cid1 = h.last_call_id(name="list_tasks")
    await h.deliver_tool_result(call_id=cid1, result="ok")
    cid2 = h.last_call_id(name="get_status")
    await h.deliver_tool_result(call_id=cid2, result="ok")

    from brain.canned_speech import CannedKey

    assert CannedKey.BATCH_CEILING_HIT in h.canned_spoken
    h.assert_no_active_demo()
    h.assert_no_in_flight()


async def test_response_timeout_recovery_speaks_canned(harness_with_tools):
    h = harness_with_tools
    h.set_timeouts(response_seconds=0.05)
    h.processor._create_reply_watchdog()
    # Don't notify the response-timeout. Wait for it to fire.
    await asyncio.sleep(0.15)

    from brain.canned_speech import CannedKey

    assert CannedKey.RESPONSE_TIMEOUT in h.canned_spoken


async def test_response_timeout_notifier_prevents_firing(harness_with_tools):
    h = harness_with_tools
    h.set_timeouts(response_seconds=0.2)
    h.processor._create_reply_watchdog()
    await asyncio.sleep(0.05)
    await h.processor._fire_reply_watchdog()
    await asyncio.sleep(0.3)

    from brain.canned_speech import CannedKey

    assert CannedKey.RESPONSE_TIMEOUT not in h.canned_spoken
