"""Screenshot capture: the broker is invoked when the agent decides
the visitor's request needs vision. In action mode the LLM sets
``decision_to_request_screenshot=True`` and the processor spawns a
fetch; in guide mode every user wake spawns a fetch unconditionally.
On the resulting ``screenshot_result`` wake the captured bytes ride
on the human marker as an ``image_url`` content block. Timeout /
widget error → ``capture_failed=True`` on the SCREENSHOT_RESULT frame
and the round still inferences with a plain-string human marker.

What this file pins:

1. Action-mode user wakes do NOT auto-fetch — the LLM gates via
   ``decision_to_request_screenshot``.
2. When the LLM does ask for vision, the processor runs a
   screenshot_result round whose human marker is multimodal.
3. On capture failure, the screenshot_result round still fires with
   a plain-string human marker (no image bytes to attach) and the
   ``capture_failed`` flag rides on ``wake_frame.data``.
4. The broker's RTVI inbound routing + shutdown cancellation still
   work end-to-end.

Guide-mode coverage (every user wake → screenshot_result) lives in
``test_guide_mode.py`` so it can stay focused on guide-specific
schema + cursor dispatch.
"""

from __future__ import annotations

import pytest

from tests.harness.fakes import (
    make_placeholder_screenshot,
)


def _human_content(harness):
    """Pull the human-marker content from the most recent LLM round."""
    last_view = harness.llm.structured_views[-1]
    return last_view.calls[-1]["messages"][1][1]


# ──────────────────────────────────────────────────────────────────────
# Action-mode: LLM gates the fetch
# ──────────────────────────────────────────────────────────────────────


async def test_user_voice_round_does_not_auto_fetch_screenshot(harness):
    """Default user-voice wake without a screenshot decision: no fetch."""
    harness.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "is_message_relevant": "relevant",
                "speech": "ok",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "decision_to_request_screenshot": False,
                "screenshot_request_context": None,
            }
        ]
    )
    assert harness.screenshots.requests == 0
    await harness.send_user("hi")
    assert harness.screenshots.requests == 0


async def test_kickoff_does_not_fetch_screenshot(harness):
    """Kickoff fires before the visitor has spoken — no question yet
    that could possibly benefit from vision."""
    harness.script_llm_outputs(
        [
            {
                "speech": "Welcome.",
            }
        ]
    )
    await harness.send_kickoff()
    assert harness.screenshots.requests == 0


async def test_tool_batch_completed_does_not_fetch_screenshot(
    harness_with_tools,
):
    """Tool result wake — agent reasons about its own tool output;
    vision isn't part of the schema. No fetch."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "is_message_relevant": "relevant",
                "speech": "Sure, looking it up.",
                "demonstration_action": "start_new",
                "demonstration_name": "lookup",
                "tool_invocations": [
                    {"name": "get_status", "arguments": {}}
                ],
                "decision_to_request_screenshot": False,
                "screenshot_request_context": None,
            },
            {
                "speech": "Status checked.",
                "demonstration_action": "end_current",
                "demonstration_name": None,
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("check the status please")
    dispatch_payload = h.batch_events[-1][1]
    call_id = dispatch_payload["tool_calls"][0]["id"]
    await h.deliver_tool_result(call_id=call_id, result={"ok": True})
    assert h.screenshots.requests == 0


# ──────────────────────────────────────────────────────────────────────
# Action-mode: when the LLM asks, the screenshot_result round attaches
# the image to its human marker
# ──────────────────────────────────────────────────────────────────────


async def test_decision_to_request_screenshot_triggers_fetch_and_attaches_image(
    harness,
):
    """End-to-end: LLM sets decision_to_request_screenshot=True on
    a user-voice round → processor spawns the fetch → resulting
    SCREENSHOT_RESULT round's human marker is multimodal with the
    captured bytes inlined as an image_url block."""
    custom = make_placeholder_screenshot(
        image_b64="QkFOTkVSX1RPS0VOXzQy",  # base64 of "BANNER_TOKEN_42"
        mime="image/jpeg",
        width=1024,
        height=768,
    )
    harness.screenshots.script_capture(custom)
    harness.script_llm_outputs(
        [
            # Round 1 (user_voice): LLM acknowledges + asks for vision.
            {
                "user_turn_status": "complete",
                "is_message_relevant": "relevant",
                "speech": "Let me take a quick look.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "decision_to_request_screenshot": True,
                "screenshot_request_context": (
                    "Visitor pointed at something on the page; need to see "
                    "what it is."
                ),
            },
            # Round 2 (screenshot_result): the actual answer. The
            # human marker for THIS round is what we assert on.
            {
                "speech": "I see what you mean.",
                "demonstration_action": "continue",
                "demonstration_name": None,
            },
        ]
    )
    await harness.send_user("what is this?")
    # The screenshot_result round is the LAST LLM call; check its marker.
    content = _human_content(harness)
    assert isinstance(content, list), (
        "screenshot_result inference must carry the captured bytes on "
        "the human marker as a multimodal content-block list"
    )
    text_block, image_block = content
    assert text_block["type"] == "text"
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["url"] == (
        "data:image/jpeg;base64,QkFOTkVSX1RPS0VOXzQy"
    )
    # Exactly one fetch — the LLM asked once, the system honoured once.
    assert harness.screenshots.requests == 1


# ──────────────────────────────────────────────────────────────────────
# Action-mode deictic flow: the screenshot_result round can dispatch
# tools, NOT just speak. (Bug guard: an earlier version of
# allowed_actions silently coerced start_new → continue on
# screenshot_result wakes, after which the two-trigger gate dropped
# every dispatched batch — visitor's request never executed even
# though the agent said it would.)
# ──────────────────────────────────────────────────────────────────────


async def test_screenshot_result_round_can_dispatch_tools_via_start_new(
    harness_with_tools,
):
    """End-to-end deictic action: visitor asked the agent to act on
    something on-screen ("create a task called X"). Round 1
    (user_voice): LLM sets ``decision_to_request_screenshot=True``.
    Round 2 (screenshot_result): with the image attached the agent
    identifies the target and emits ``demonstration_action='start_new'``
    + ``tool_invocations=[create_task]``. The processor MUST
    honour the dispatch — neither coerce the demonstration_action to
    ``continue`` nor drop the tool via the two-trigger gate.
    """
    h = harness_with_tools
    h.screenshots.script_capture(
        make_placeholder_screenshot(
            image_b64="UExBQ0VfSE9MREVS",  # base64 of "PLACE_HOLDER"
            mime="image/jpeg",
            width=1024, height=768,
        ),
    )
    h.script_llm_outputs(
        [
            # Round 1 — agent asks for vision.
            {
                "user_turn_status": "complete",
                "is_message_relevant": "relevant",
                "speech": "Let me take a quick look at your screen.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "decision_to_request_screenshot": True,
                "screenshot_request_context": (
                    "Visitor pointed at a task on the page; need to "
                    "find which one to create the duplicate of."
                ),
            },
            # Round 2 — screenshot_result, agent dispatches the tool.
            {
                "speech": "Got it — creating the task now.",
                "demonstration_action": "start_new",
                "demonstration_name": "Create the task visitor pointed at",
                "tool_invocations": [
                    {
                        "name": "create_task",
                        "arguments": {"name": "Ship the release"},
                    }
                ],
            },
        ]
    )
    await h.send_user("Create the task the visitor's looking at.")

    # The fetch happened exactly once on the user-voice round.
    assert h.screenshots.requests == 1
    # The screenshot_result round actually dispatched a batch — this
    # is the regression guard.
    dispatch_events = [e for e in h.batch_events if e[0] == "dispatch"]
    assert dispatch_events, (
        "screenshot_result emitted start_new + tool_invocations but no "
        "batch was dispatched — the allowed_actions map must include "
        "screenshot_result, otherwise start_new is silently coerced to "
        "continue and the two-trigger gate drops the tool"
    )
    payload = dispatch_events[-1][1]
    assert len(payload["tool_calls"]) == 1
    assert payload["tool_calls"][0]["name"] == "create_task"
    # And start_new actually started a new demonstration.
    assert h.processor._active_demonstration is not None, (
        "start_new on screenshot_result must create an active demo"
    )
    assert (
        h.processor._active_demonstration.name
        == "Create the task visitor pointed at"
    )


# ──────────────────────────────────────────────────────────────────────
# Capture failure path: screenshot_result still fires text-only
# ──────────────────────────────────────────────────────────────────────


async def test_capture_failed_still_runs_screenshot_result_round(harness):
    """Broker returned None (timeout / widget error). The processor
    still enqueues a SCREENSHOT_RESULT frame with capture_failed=True
    and the screenshot_result round runs with a plain-string human
    marker — a screenshot failure must NEVER block inference."""
    harness.screenshots.script_timeout()
    harness.script_llm_outputs(
        [
            # Round 1 (user_voice): LLM asks for vision.
            {
                "user_turn_status": "complete",
                "is_message_relevant": "relevant",
                "speech": "Let me take a look.",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "decision_to_request_screenshot": True,
                "screenshot_request_context": (
                    "Visitor referred to something on screen — checking "
                    "the page."
                ),
            },
            # Round 2 (screenshot_result, capture_failed): contextual
            # apology in the LLM's own voice.
            {
                "speech": (
                    "Sorry, couldn't see your screen this time — could "
                    "you describe what you're looking at?"
                ),
                "demonstration_action": "continue",
                "demonstration_name": None,
            },
        ]
    )
    await harness.send_user("what is this?")
    # Broker was asked exactly once (the action-mode flow asks once
    # per decision-to-request).
    assert harness.screenshots.requests == 1
    # Round 2 did fire — the apology went into history.
    apology = harness.assistant_speech_history[-1]["content"]
    assert "couldn't see" in apology.lower() or "describe" in apology.lower()
    # Human marker is plain string on the screenshot_result round
    # because there are no image bytes to attach.
    content = _human_content(harness)
    assert isinstance(content, str), (
        "capture_failed → no image bytes → the screenshot_result round "
        "must inference with a plain-string human marker"
    )


# ──────────────────────────────────────────────────────────────────────
# RTVI inbound: screenshot_response routes to the broker
# ──────────────────────────────────────────────────────────────────────


async def test_screenshot_response_client_message_routes_to_real_broker():
    """Verify that the inbound ``screenshot_response`` RTVI client
    message routes into the broker's ``resolve()``. We drive the
    routing path directly (no background round) to avoid harness
    pump-loop races: register a real broker, kick off a manual
    ``request()``, deliver the matching client message, assert the
    bytes flow through."""
    from brain.screenshot_service import ScreenshotService
    from tests.harness.processor_harness import ProcessorHarness

    h = ProcessorHarness()
    try:
        sent: list[dict] = []

        async def sender(payload: dict) -> None:
            sent.append(payload)

        real = ScreenshotService(sender=sender, default_timeout_seconds=2.0)
        h.processor._screenshot_service = real  # type: ignore[attr-defined]

        import asyncio

        request_task = asyncio.create_task(real.request())
        for _ in range(50):
            if sent:
                break
            await asyncio.sleep(0.001)
        assert sent, "broker never sent request_screenshot"
        request_id = sent[0]["request_id"]

        await h.rtvi.deliver_client_message(
            type="screenshot_response",
            data={
                "request_id": request_id,
                "image_b64": "QkFOTkVSX1RPS0VOXzQy",
                "mime": "image/jpeg",
                "width": 800,
                "height": 600,
            },
        )

        captured = await asyncio.wait_for(request_task, timeout=1.0)
        assert captured is not None
        assert captured.image_b64 == "QkFOTkVSX1RPS0VOXzQy"
        assert captured.mime == "image/jpeg"
        assert captured.width == 800
    finally:
        await h.shutdown()


# ──────────────────────────────────────────────────────────────────────
# Cleanup contract — pending awaiters released on shutdown
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_releases_in_flight_screenshot_awaiters():
    """Pipeline tear-down must call ``cancel_all`` so any awaiting
    inference round resolves immediately rather than hanging."""
    from brain.screenshot_service import ScreenshotService
    from tests.harness.processor_harness import ProcessorHarness

    h = ProcessorHarness()
    sent: list[dict] = []

    async def sender(payload: dict) -> None:
        sent.append(payload)

    real = ScreenshotService(sender=sender, default_timeout_seconds=10.0)
    h.processor._screenshot_service = real  # type: ignore[attr-defined]

    import asyncio

    request_task = asyncio.create_task(real.request())
    for _ in range(100):
        if sent:
            break
        await asyncio.sleep(0.001)

    await h.shutdown()
    result = await asyncio.wait_for(request_task, timeout=1.0)
    assert result is None
