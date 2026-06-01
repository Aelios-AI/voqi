"""Idle-session timer (cancel-and-rearm design).

Two-stage:
  Stage 1: ``_idle_warning_after_seconds`` of no qualifying user turn
           → SYSTEM "are you still there?" wake.
  Stage 2: ``_idle_end_after_warning_seconds`` of further silence →
           CannedKey.SESSION_IDLE_GOODBYE + CancelTaskFrame upstream.

Two independent concerns at the dispatcher (see ``_run_one_round``
caller in ``_message_pump_loop``):
  Concern A: a VALID user turn (text always, voice classified
             ``relevant``) cancels both timers via _cancel_idle_timer.
  Concern B: every natural turn end calls ``_arm_idle_timer_if_appropriate``,
             which arms stage 1 IFF no timer is currently armed AND
             batch_state is not in_flight AND session is alive.
"""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import CancelTaskFrame

from brain.canned_speech import CannedKey
from brain.frames import MessageType


def _is_armed(t):
    return t is not None and not t.done()


# ── ARM-IF-APPROPRIATE (concern B) ────────────────────────────────────


async def test_arm_after_kickoff_natural_end(harness):
    """Kickoff round completes naturally with state idle → stage 1
    armed. (No timer was running; conditions hold.)"""
    h = harness
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
        ]
    )
    await h.send_kickoff()
    assert _is_armed(h.processor._idle_warning_task)
    assert not _is_armed(h.processor._idle_end_task)


async def test_arm_skipped_when_in_flight(harness_with_tools):
    """User triggers a tool dispatch → state in_flight after the round
    → arm-if-appropriate must NOT arm (agent is doing real work)."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "speech": "Listing now.",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ]
    )
    await h.send_user("show me my tasks")
    h.assert_in_flight(expected_size=1)
    assert not _is_armed(h.processor._idle_warning_task)
    assert not _is_armed(h.processor._idle_end_task)


async def test_arm_when_pending_confirmation(harness_with_tools):
    """Confirmable batch parked → state pending_confirmation. Visitor
    is being waited on; idle timer must arm."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "speech": "Delete? confirm.",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [
                    {"name": "delete_task", "arguments": {"id": "p1"}}
                ],
            },
        ]
    )
    await h.send_kickoff()
    await h.send_user("delete p1")
    h.assert_pending(confirmable=["delete_task"])
    assert _is_armed(h.processor._idle_warning_task)


async def test_arm_after_tool_batch_resolved_back_to_idle(harness_with_tools):
    """Tool batch resolves and the agent ends the demo → state idle →
    arm-if-appropriate kicks in (no timer was armed during in_flight)."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            # Turn 1: dispatch list_tasks.
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "speech": "Listing.",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            # Turn 2 (tool_batch_completed): wrap up + end the demo.
            {
                "speech": "Done.",
                "demonstration_action": "end_current",
                "tool_invocations": [],
            },
        ]
    )
    await h.send_kickoff()
    await h.send_user("show me my tasks")
    cid = h.last_call_id(name="list_tasks")
    assert cid is not None
    await h.deliver_tool_result(call_id=cid, result=[{"id": "t-1"}])
    # Demo over, no in-flight batch.
    h.assert_no_active_demo()
    h.assert_no_in_flight()
    assert _is_armed(h.processor._idle_warning_task)


# ── VALID-INPUT-CANCELS (concern A) ───────────────────────────────────


async def test_valid_text_cancels_running_timer(harness_with_tools):
    """Stage 1 armed (post-kickoff), visitor types something →
    timer cancelled and a fresh one armed (different task object)."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            {
                "speech": "Sure.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_kickoff()
    first_task = h.processor._idle_warning_task
    assert _is_armed(first_task)

    await h.send_text_message("hello there")
    second_task = h.processor._idle_warning_task
    assert _is_armed(second_task)
    assert second_task is not first_task  # cancel-and-rearm replaced it


async def test_valid_relevant_voice_cancels_running_timer(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "speech": "Sure.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_kickoff()
    first_task = h.processor._idle_warning_task
    assert _is_armed(first_task)

    await h.send_user("question about the product")
    assert h.processor._idle_warning_task is not first_task
    assert _is_armed(h.processor._idle_warning_task)


async def test_off_topic_voice_does_not_cancel_timer(harness_with_tools):
    """Off-topic voice must NOT cancel the running timer. The same
    task object survives across the off-topic turn."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            {
                "is_message_relevant": "off_topic",
                "user_turn_status": "complete",
                "speech": "",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_kickoff()
    first_task = h.processor._idle_warning_task
    assert _is_armed(first_task)

    await h.send_user("...yeah send it later")
    assert h.processor._idle_warning_task is first_task
    assert _is_armed(first_task)


async def test_inference_failure_on_text_still_counts_as_valid(harness_with_tools):
    """Even when the LLM round fails, a TEXT wake is treated as a
    valid user turn — visitor explicitly typed something. The timer
    should be cancelled and rearmed."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
        ]
    )
    # Second round (the text turn) will throw inside the LLM.
    h.llm.script_error()
    await h.send_kickoff()
    first_task = h.processor._idle_warning_task
    assert _is_armed(first_task)

    await h.send_text_message("hello")
    # New timer task replaced the first one (cancel-and-rearm fired
    # because text is always valid even on inference failure).
    assert h.processor._idle_warning_task is not first_task


# ── STAGE 1 FIRES ─────────────────────────────────────────────────────


async def test_stage_one_fires_after_threshold(harness):
    """Shrink the warning threshold to 50ms; verify a SYSTEM frame
    gets enqueued by the warning runner."""
    h = harness
    h.set_timeouts(idle_warning_after=0.05, idle_end_after_warning=10.0)
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            # NOTE: no second LLM script entry — the warning is now
            # canned speech, not an LLM round.
        ]
    )
    await h.send_kickoff()
    assert _is_armed(h.processor._idle_warning_task)

    # Wait for the warning task to fire.
    await asyncio.sleep(0.15)
    await h.pump_until_idle()

    # Warning task is done (fired); end task is now armed.
    assert h.processor._idle_warning_task is None or h.processor._idle_warning_task.done()
    assert _is_armed(h.processor._idle_end_task)
    # Canned IDLE_WARNING was spoken — recognisable by the English copy
    # in canned_speech.py ("If there's nothing else you need help with").
    assert any(
        "close the session" in s.get("content", "")
        for s in h.assistant_speech_history
    )


# ── STAGE 2: VISITOR RESPONSES ────────────────────────────────────────


async def test_stage_two_end_session_closes_immediately(harness_with_tools):
    """Stage 2 armed; visitor confirms end → CannedKey-flavoured
    speech pushed, both timers cancelled, CancelTaskFrame upstream."""
    h = harness_with_tools
    h.set_timeouts(idle_warning_after=0.05, idle_end_after_warning=5.0)
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            # NOTE: idle warning is canned now, no LLM script entry.
            # Visitor's reply to the warning: end_session.
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "idle_warning_resolution": "end_session",
                "speech": "Talk to you later.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_kickoff()
    await asyncio.sleep(0.15)
    await h.pump_until_idle()
    assert _is_armed(h.processor._idle_end_task)

    await h.send_user("yeah you can close it")
    # Both timers cancelled.
    assert not _is_armed(h.processor._idle_warning_task)
    assert not _is_armed(h.processor._idle_end_task)
    # CancelTaskFrame pushed upstream.
    assert any(isinstance(f, CancelTaskFrame) for f in h.pushed_frames)


async def test_stage_two_continue_session_resets_both_timers(harness_with_tools):
    """Stage 2 armed; visitor says something valid + continue_session
    → both timers cancelled, fresh stage 1 armed."""
    h = harness_with_tools
    h.set_timeouts(idle_warning_after=0.05, idle_end_after_warning=10.0)
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "idle_warning_resolution": "continue_session",
                "speech": "Sure, I had a question about exports.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_kickoff()
    await asyncio.sleep(0.15)
    await h.pump_until_idle()
    end_task_before = h.processor._idle_end_task
    assert _is_armed(end_task_before)

    await h.send_user("no wait, I had a question about exports")
    # Stage 2 task cancelled.
    assert not _is_armed(end_task_before)
    assert h.processor._idle_end_task is None or h.processor._idle_end_task.done()
    # Fresh stage 1 armed.
    assert _is_armed(h.processor._idle_warning_task)


async def test_stage_two_off_topic_voice_does_not_cancel(harness_with_tools):
    """Stage 2 armed; off-topic voice arrives → both timers untouched."""
    h = harness_with_tools
    h.set_timeouts(idle_warning_after=0.05, idle_end_after_warning=10.0)
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            {
                "is_message_relevant": "off_topic",
                "user_turn_status": "complete",
                "speech": "",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_kickoff()
    await asyncio.sleep(0.15)
    await h.pump_until_idle()
    end_task_before = h.processor._idle_end_task
    assert _is_armed(end_task_before)

    await h.send_user("hey can you grab me a coffee")
    # Stage 2 still ticking.
    assert h.processor._idle_end_task is end_task_before
    assert _is_armed(end_task_before)


async def test_stage_two_force_end_when_grace_elapses(harness):
    """Stage 2 armed; nobody replies → end runner fires, speaks the
    canned goodbye, pushes CancelTaskFrame upstream."""
    h = harness
    # Stage 1 fires at 50ms, stage 2 at 50ms after that. Total ≤120ms.
    h.set_timeouts(idle_warning_after=0.05, idle_end_after_warning=0.05)
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
        ]
    )
    await h.send_kickoff()
    # Wait long enough for BOTH stages to fire and the goodbye to land.
    await asyncio.sleep(0.4)
    await h.pump_until_idle()
    # CancelTaskFrame pushed upstream — bot tearing down.
    assert any(isinstance(f, CancelTaskFrame) for f in h.pushed_frames)
    # _ended flag set by the end runner.
    assert h.processor._ended


# ── CORRUPT-STATE GUARD ──────────────────────────────────────────────


async def test_tool_batch_completed_with_armed_timer_cancels_defensively(
    harness_with_tools,
):
    """Belt-and-suspenders. The arm helper should never let a timer
    coexist with an in-flight batch, but if state ever corrupts and a
    tool_batch_completed wake fires while a timer is armed, cancel
    defensively before processing — tools just resolved, the visitor
    was effectively active. Asserts the runtime guard, not normal
    behaviour."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
            # Turn 1: dispatch list_tasks.
            {
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "speech": "Listing.",
                "demonstration_action": "start_new",
                "demonstration_name": "browse",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
            # Turn 2 (tool_batch_completed): wrap up.
            {"speech": "Done.", "demonstration_action": "end_current", "tool_invocations": []},
        ]
    )
    await h.send_kickoff()
    await h.send_user("show me my tasks")
    h.assert_in_flight(expected_size=1)
    cid = h.last_call_id(name="list_tasks")
    assert cid is not None

    # Force corrupt state: pretend a timer is armed during the in-flight
    # window (this would only happen via a bug elsewhere; we're
    # asserting the guard in the dispatcher catches it).
    async def _noop():
        await asyncio.sleep(60)

    h.processor._idle_warning_task = asyncio.get_running_loop().create_task(_noop())

    await h.deliver_tool_result(call_id=cid, result=[])
    # Guard tripped: the corrupt timer was cancelled before the
    # tool_batch_completed round ran. After the round a legitimate fresh
    # timer may have armed (state went idle), but the original forced
    # task must no longer be the live one.
    armed_now = h.processor._idle_warning_task
    assert armed_now is None or armed_now.get_name() == "in-app-idle-warning", (
        "guard did not clear the corrupt timer before running the round"
    )


# ── INTERRUPTION DOES NOT CANCEL ──────────────────────────────────────


async def test_interruption_alone_does_not_cancel_timer(harness):
    """Pipecat InterruptionFrame can fire on any voice activity (room
    noise included). Idle timer must NOT cancel from that alone — only
    the downstream relevance verdict cancels."""
    from pipecat.frames.frames import InterruptionFrame
    from pipecat.processors.frame_processor import FrameDirection

    h = harness
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
        ]
    )
    await h.send_kickoff()
    pre_task = h.processor._idle_warning_task
    assert _is_armed(pre_task)

    await h.processor.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    # Same task object survives — not cancelled by the interruption.
    assert h.processor._idle_warning_task is pre_task
    assert _is_armed(pre_task)


# ── SCHEMA THREADING ──────────────────────────────────────────────────


async def test_schema_exposes_idle_warning_resolution_when_stage_two_armed(
    harness_with_tools,
):
    """Verify the per-round schema reflects the live stage-2 state.
    Forces stage 2 by pretending the end task is armed."""
    from brain.agent_output import build_in_app_schema

    h = harness_with_tools

    async def _noop():
        await asyncio.sleep(60)

    # Pretend stage 2 is armed by parking a sleeping task on the field.
    h.processor._idle_end_task = asyncio.get_running_loop().create_task(_noop())
    try:
        s = build_in_app_schema(
            wake_mode="user_voice",
            batch_state="idle",
            tools=h.config.tools,
            idle_stage_two_armed=h.processor._is_idle_stage_two_armed(),
        )
        assert "idle_warning_resolution" in s["properties"]
        assert "idle_warning_resolution" in s["required"]
    finally:
        h.processor._idle_end_task.cancel()
        try:
            await h.processor._idle_end_task
        except asyncio.CancelledError:
            pass
        h.processor._idle_end_task = None


# Multilingual translation coverage for every CannedKey (including
# SESSION_IDLE_GOODBYE) is parameterised over all keys in
# layer1_unit/test_canned_speech.test_every_key_has_full_translation_set
# and test_every_translation_non_empty. No need to re-assert here.


# ── WARNING IS CANNED, NOT INFERENCE ─────────────────────────────────


async def test_warning_is_canned_speech_not_llm_round(harness):
    """The 3-minute idle warning is a CANNED utterance, not an LLM
    round. The fixed text is deterministic and inference is wasted
    for a fixed string. We assert:
      1. The canned speech for ``CannedKey.IDLE_WARNING`` lands in
         the conversation history (so subsequent LLM rounds see the
         warning was issued).
      2. NO additional LLM scripted output was consumed beyond the
         kickoff round (proving we didn't run a second LLM round)."""
    from brain.canned_speech import (
        _TRANSLATIONS,
        CannedKey,
        render_canned,
    )

    h = harness
    h.set_timeouts(idle_warning_after=0.05, idle_end_after_warning=10.0)
    h.script_llm_outputs(
        [
            {"speech": "Hi.", "demonstration_action": "continue", "demonstration_name": None},
        ]
    )
    await h.send_kickoff()
    initial_outputs = len(h.llm.outputs)
    assert initial_outputs == 0  # kickoff consumed the only entry

    await asyncio.sleep(0.15)
    await h.pump_until_idle()

    # The English canned IDLE_WARNING text should be recognisable in
    # the assistant transcript. Sanity-check by computing the
    # rendered text and looking for a substring.
    rendered = render_canned(CannedKey.IDLE_WARNING, "en")
    # Pick a stable phrase from the rendered text.
    sample_phrase = "close the session"
    assert sample_phrase in rendered.lower()
    assert any(
        sample_phrase in s.get("content", "").lower()
        for s in h.assistant_speech_history
    )
    # No additional LLM script was consumed — proving the canned
    # path didn't run an inference.
    assert len(h.llm.outputs) == 0  # untouched

    # Sanity: IDLE_WARNING is multilingual.
    assert "en" in _TRANSLATIONS[CannedKey.IDLE_WARNING]
    assert len(_TRANSLATIONS[CannedKey.IDLE_WARNING]) >= 20
