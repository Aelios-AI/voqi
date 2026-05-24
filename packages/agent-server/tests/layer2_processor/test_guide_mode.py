"""Guide mode end-to-end on the processor harness.

Pins these contracts:

1. **User-wake short-circuit.** In guide mode, USER_MESSAGE /
   TEXT_MESSAGE wakes do NOT run the LLM. The processor spawns a
   screenshot fetch and the LLM only runs at the SCREENSHOT_RESULT
   wake that follows.
2. **Tool list is empty in guide mode.** The schema must not expose
   ``tool_invocations`` regardless of host registrations (the bot
   drops them when mode=='guide', so verify the gate fires).
3. **point_to dispatches as a ``guide_cursor`` server message.** When
   the LLM returns a ``point_to`` object, the processor pushes a
   ``guide_cursor`` RTVI server message carrying the normalized coords
   and label. ``null`` point_to → no cursor message.
4. **Capture-failed branch** — the SCREENSHOT_RESULT frame can carry
   ``capture_failed=True`` (timeout / canvas error). The LLM still
   runs and apologises contextually; ``point_to`` MUST be null.
5. **Off-topic / incomplete suppression.** When the original wake was
   voice and the LLM classifies it ``off_topic`` or
   ``user_turn_status=incomplete_*``, the cursor is dropped; brief
   speech goes through.
6. **Kickoff is shared.** Both modes greet identically — schema is
   speech-only, behaviour is unchanged.
7. **Idle stage-2 end_session works in guide mode.** The visitor's
   "yeah I'm done" reply triggers the same close-immediately path
   as action mode.
"""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import CancelTaskFrame

# ──────────────────────────────────────────────────────────────────────
# 1. User-wake short-circuit: no LLM at user_voice / user_text
# ──────────────────────────────────────────────────────────────────────


async def test_guide_mode_user_voice_does_not_run_llm_at_user_wake(
    harness_guide_mode,
):
    """Guide mode skips the LLM entirely on the voice user wake — the
    processor spawns a screenshot fetch and waits for SCREENSHOT_RESULT.
    Once the result lands, the LLM runs ONCE on that wake."""
    h = harness_guide_mode
    # Script ONE LLM round for the screenshot_result wake. If the
    # processor incorrectly ran the LLM on the user_voice wake too,
    # the queue would underflow on the second round.
    h.script_llm_outputs(
        [
            {
                "speech": "I see what you mean — click the gear in the top right.",
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "point_to": {"x": 0.92, "y": 0.08, "label": "Click here"},
            }
        ]
    )
    await h.send_user("where do I find settings?")
    # Exactly one LLM round consumed (the screenshot_result one).
    # FakeChatOpenAI's `outputs` deque holds remaining scripts.
    assert len(h.llm.outputs) == 0
    # Two screenshot requests would mean we got two LLM rounds; we
    # only expect one fetch (spawned at the user wake) plus one round
    # on the result.
    assert h.screenshots.requests == 1
    # Cursor was dispatched.
    cursor_msgs = h.rtvi.server_messages_of_type("guide_cursor")
    assert len(cursor_msgs) == 1
    assert cursor_msgs[0]["x"] == 0.92
    assert cursor_msgs[0]["y"] == 0.08
    assert cursor_msgs[0]["label"] == "Click here"


async def test_guide_mode_user_text_does_not_run_llm_at_user_wake(
    harness_guide_mode,
):
    """Same short-circuit applies to typed text. The voice gates
    don't apply (text is always for-you), but the screenshot-first
    rule still does."""
    h = harness_guide_mode
    h.script_llm_outputs(
        [
            {
                "speech": "Sure — open the Plans tab on the left.",
                "point_to": {"x": 0.05, "y": 0.42, "label": "Plans"},
            }
        ]
    )
    await h.send_text_message("show me plans")
    assert len(h.llm.outputs) == 0
    assert h.screenshots.requests == 1
    assert h.rtvi.last_of_type("guide_cursor") is not None


async def test_guide_mode_screenshot_result_uses_guide_schema(
    harness_guide_mode,
):
    """Verify the schema actually used for the LLM call has guide
    shape: no tool_invocations, has point_to."""
    h = harness_guide_mode
    h.script_llm_outputs(
        [
            {
                "speech": "Right here.",
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "point_to": None,
            }
        ]
    )
    await h.send_user("just checking")
    # Inspect the schema the FakeChatOpenAI saw on its only call.
    assert len(h.llm.structured_views) == 1
    schema = h.llm.structured_views[0].schema["parameters"]
    assert "tool_invocations" not in schema["properties"]
    assert "demonstration_action" not in schema["properties"]
    assert "point_to" in schema["properties"]


# ──────────────────────────────────────────────────────────────────────
# 2. Cursor dispatch: point_to=null → no guide_cursor message
# ──────────────────────────────────────────────────────────────────────


async def test_guide_mode_no_cursor_when_point_to_is_null(
    harness_guide_mode,
):
    """Pure-question turn: visitor asked something that doesn't need
    a click target. point_to=null and the processor MUST NOT push a
    guide_cursor server message."""
    h = harness_guide_mode
    h.script_llm_outputs(
        [
            {
                "speech": "It's a billing dashboard for tracking invoices.",
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "point_to": None,
            }
        ]
    )
    await h.send_user("what is this dashboard?")
    assert h.rtvi.server_messages_of_type("guide_cursor") == []


# ──────────────────────────────────────────────────────────────────────
# 3. Capture-failed branch: LLM still runs, no cursor, contextual speech
# ──────────────────────────────────────────────────────────────────────


async def test_guide_mode_capture_failed_does_not_dispatch_cursor(
    harness_guide_mode,
):
    """Widget capture failed (CSP / canvas error / timeout). The
    processor still routes to the LLM at screenshot_result wake (with
    capture_failed=True on the frame data); the LLM apologises
    contextually but MUST set point_to=null since there's no image to
    pin coordinates against."""
    h = harness_guide_mode
    # Force the screenshot fetch to fail.
    h.screenshots.script_timeout()
    h.script_llm_outputs(
        [
            {
                "speech": "Sorry — couldn't grab your screen this time, "
                          "could you describe what you're looking at?",
                "is_message_relevant": "relevant",
                "user_turn_status": "complete",
                "point_to": None,
            }
        ]
    )
    await h.send_user("how do I get to settings?")
    # LLM still ran (one round consumed).
    assert len(h.llm.outputs) == 0
    # Schema saw the screenshot_result wake — guide branch.
    schema = h.llm.structured_views[0].schema["parameters"]
    assert "point_to" in schema["properties"]
    # No cursor dispatched.
    assert h.rtvi.server_messages_of_type("guide_cursor") == []


# ──────────────────────────────────────────────────────────────────────
# 4. Off-topic / incomplete: cursor suppressed
# ──────────────────────────────────────────────────────────────────────


async def test_guide_mode_off_topic_voice_suppresses_cursor(
    harness_guide_mode,
):
    """Visitor's mic picked up a side conversation. Even if the LLM
    accidentally provided a point_to, the off_topic gate must drop it
    along with any other action."""
    h = harness_guide_mode
    h.script_llm_outputs(
        [
            {
                "speech": "I think that one wasn't aimed at me.",
                "is_message_relevant": "off_topic",
                "user_turn_status": "complete",
                # The LLM SHOULDN'T set point_to on off_topic, but the
                # processor must defend against it anyway.
                "point_to": {"x": 0.5, "y": 0.5, "label": "won't render"},
            }
        ]
    )
    await h.send_user("yeah grab me a coffee")
    assert h.rtvi.server_messages_of_type("guide_cursor") == []


async def test_guide_mode_incomplete_voice_suppresses_cursor(
    harness_guide_mode,
):
    """Visitor paused mid-clause. We expect a brief warm nudge but
    no cursor — pointing at something based on a half-formed thought
    would be jumping the gun."""
    h = harness_guide_mode
    h.script_llm_outputs(
        [
            {
                "speech": "Sorry — what were you going to say?",
                "is_message_relevant": "relevant",
                "user_turn_status": "incomplete_short",
                "point_to": None,
            }
        ]
    )
    await h.send_user("can you show me where the…")
    assert h.rtvi.server_messages_of_type("guide_cursor") == []


# ──────────────────────────────────────────────────────────────────────
# 5. Kickoff: speech-only, same as action mode
# ──────────────────────────────────────────────────────────────────────


async def test_guide_mode_kickoff_is_speech_only(harness_guide_mode):
    """Kickoff fires before the visitor has spoken — no cursor, no
    screenshot-fetch hijack. The processor should just run the LLM
    on the kickoff wake."""
    h = harness_guide_mode
    h.script_llm_outputs(
        [
            {"speech": "Hi! I can show you around — just ask."}
        ]
    )
    await h.send_kickoff()
    assert len(h.llm.outputs) == 0
    schema = h.llm.structured_views[0].schema["parameters"]
    # Kickoff schema is speech-only in BOTH modes.
    assert set(schema["properties"].keys()) == {"speech"}
    assert h.rtvi.server_messages_of_type("guide_cursor") == []


# ──────────────────────────────────────────────────────────────────────
# 6. Tools dropped: even if some old tool list slipped through, schema
#    in guide mode must still suppress tool_invocations.
# ──────────────────────────────────────────────────────────────────────


async def test_guide_mode_suppresses_tool_invocations_with_stale_tools(
    monkeypatch,
):
    """Defence-in-depth: if a stale runtime config leaks tools into a
    guide-mode session, the bot-side schema must STILL drop them."""
    from tests.harness.processor_harness import (
        ProcessorHarness,
        make_runtime_config,
        tool,
    )

    cfg = make_runtime_config(
        mode="guide",
        tools=[tool("ghost_tool", description="should never appear")],
    )
    h = ProcessorHarness(runtime_config=cfg)
    try:
        h.script_llm_outputs(
            [
                {
                    "speech": "Click the New button.",
                    "is_message_relevant": "relevant",
                    "user_turn_status": "complete",
                    "point_to": {"x": 0.8, "y": 0.1, "label": "New"},
                }
            ]
        )
        await h.send_user("how do I create one?")
        schema = h.llm.structured_views[0].schema["parameters"]
        assert "tool_invocations" not in schema["properties"]
    finally:
        await h.shutdown()


# ──────────────────────────────────────────────────────────────────────
# 7. Idle stage-2 end_session works in guide mode
# ──────────────────────────────────────────────────────────────────────


async def test_guide_mode_idle_timer_skipped_while_screenshot_in_flight(
    harness_guide_mode,
):
    """The idle timer's "still there?" check-in must not fire while
    the agent is mid-fetching a screenshot. Same family of rules as
    the existing batch_state==in_flight gate: 'agent actively
    working on the visitor's behalf' shouldn't trigger an idle
    nudge. Especially important in guide mode where every user wake
    spawns a fetch that briefly holds pending_count > 0.

    We verify by directly calling ``_arm_idle_timer_if_appropriate``
    while the FakeScreenshotService reports pending_count > 0 and
    asserting no warning task gets armed.
    """
    h = harness_guide_mode
    h.processor._session_started = True
    # Prime the screenshot service to look like a fetch is in flight.
    h.screenshots._in_flight = 1
    try:
        await h.processor._arm_idle_timer_if_appropriate()
        assert h.processor._idle_warning_task is None, (
            "Idle warning timer should NOT be armed while a screenshot "
            "fetch is in flight (pending_count > 0)."
        )
    finally:
        h.screenshots._in_flight = 0


async def test_guide_mode_idle_timer_skipped_when_queue_has_pending_frame(
    harness_guide_mode,
):
    """Companion to the in-flight-screenshot gate: the timer also
    must not arm when there's a pending frame on the priority queue.
    Anything sitting there (a SCREENSHOT_RESULT whose bytes just
    landed, a TOOL_BATCH_COMPLETED awaiting inference, a queued
    USER_MESSAGE) means the agent is about to inference and speak —
    firing 'still there?' between enqueue and dequeue would race the
    agent's own response."""
    from brain.frames import (
        InAppMessageFrame,
        MessageType,
        RankedEnvelope,
    )
    h = harness_guide_mode
    h.processor._session_started = True

    # Inject a SCREENSHOT_RESULT frame directly onto the queue,
    # bypassing the pump (so it stays parked through the assertion).
    # This simulates the brief window between
    # ``_fetch_screenshot_and_enqueue`` enqueuing the frame and the
    # message_processor dequeuing it.
    h.processor._wake_queue.put_nowait(
        RankedEnvelope(
            priority=1,
            frame=InAppMessageFrame(
                message="(test) pending screenshot result",
                message_type=MessageType.SCREENSHOT_RESULT,
                put_back_when_interrupted=True,
                data={},
            ),
        )
    )
    try:
        await h.processor._arm_idle_timer_if_appropriate()
        assert h.processor._idle_warning_task is None, (
            "Idle warning timer should NOT be armed while a frame is "
            "waiting on the priority queue — the agent is about to "
            "inference + respond, and the check-in would race that."
        )
    finally:
        # Drain the parked frame so the test's harness shutdown is clean.
        try:
            h.processor._wake_queue.get_nowait()
            h.processor._wake_queue.task_done()
        except Exception:
            pass


async def test_guide_mode_idle_timer_arms_after_screenshot_resolves(
    harness_guide_mode,
):
    """Counterpart of the gate test above: once pending_count drops
    back to 0, the same call DOES arm. Pinning that the gate's
    ONLY blocker is the in-flight count, not some incidental state
    change introduced by guide mode."""
    h = harness_guide_mode
    h.processor._session_started = True
    assert h.screenshots.pending_count == 0
    try:
        await h.processor._arm_idle_timer_if_appropriate()
        assert h.processor._idle_warning_task is not None
        assert not h.processor._idle_warning_task.done()
    finally:
        if h.processor._idle_warning_task and not h.processor._idle_warning_task.done():
            h.processor._idle_warning_task.cancel()
            try:
                await h.processor._idle_warning_task
            except asyncio.CancelledError:
                pass


async def test_guide_mode_idle_stage_two_end_session(harness_guide_mode):
    """Visitor was just asked 'are you still there?', the 60s grace
    is running, and they answer 'yeah, I'm done'. Same close-now
    semantics as action mode: session_ending RTVI message dispatched
    and a CancelTaskFrame is pushed upstream."""
    h = harness_guide_mode

    # Force stage-2 armed by parking a long-running task on the
    # processor's _idle_end_task field — same trick the action-mode
    # idle tests use to inspect schema gating without waiting for
    # real timers to elapse.
    async def _noop():
        await asyncio.sleep(60)

    h.processor._idle_end_task = asyncio.get_running_loop().create_task(_noop())
    try:
        h.script_llm_outputs(
            [
                {
                    "speech": "Take care!",
                    "is_message_relevant": "relevant",
                    "user_turn_status": "complete",
                    "idle_warning_resolution": "end_session",
                    "point_to": None,
                }
            ]
        )
        await h.send_user("yeah, you can close it")
        ending = h.rtvi.server_messages_of_type("session_ending")
        assert len(ending) == 1
        assert ending[0].get("reason") == "visitor_confirmed_end"
        # CancelTaskFrame pushed upstream — same teardown as action mode.
        assert any(isinstance(f, CancelTaskFrame) for f in h.pushed_frames)
    finally:
        if h.processor._idle_end_task and not h.processor._idle_end_task.done():
            h.processor._idle_end_task.cancel()
            try:
                await h.processor._idle_end_task
            except asyncio.CancelledError:
                pass
