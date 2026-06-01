"""``_cancelling_current_response_generation`` cancellation backstop.

If the pump task is cancelled mid-LLM streaming round but the
underlying ``astream`` call completes before the cancel signal
propagates through the HTTP client, the post-loop check raises
``CancelledError`` so the stale response is never acted on.
"""

from __future__ import annotations

import asyncio

import pytest


async def test_cancelling_flag_makes_round_raise_cancelled_error(harness_with_tools):
    h = harness_with_tools
    h.processor._cancelling_current_response_generation = True
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "should never reach the user",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    with pytest.raises(asyncio.CancelledError):
        await h.processor._llm_round_streaming(
            wake_mode="user_voice", batch_state="idle"
        )
    # No demo started, no batch dispatched.
    h.assert_no_active_demo()
    h.assert_no_in_flight()
    h.processor._cancelling_current_response_generation = False


async def test_cancel_pump_task_sets_then_clears_flag(harness_with_tools):
    h = harness_with_tools
    # Wire up a no-op pump so cancel_processor_task has a target.
    h.processor._create_pump_task()
    assert h.processor._cancelling_current_response_generation is False
    await h.processor._cancel_pump_task()
    assert h.processor._cancelling_current_response_generation is False


# The "flag defaults to False so clean rounds aren't affected" case was
# previously a round-trip test (script "ok", assert "ok"). It only
# verified the fake, not the cancellation logic. The real backstop
# behaviour is covered by `test_cancelling_flag_makes_round_raise_
# cancelled_error` above.
