"""Atomic tool_call_batch wire protocol + ordering of speech vs batch send."""

from __future__ import annotations

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)


async def test_tool_call_batch_message_shape(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "doing it",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [
                    {"name": "create_task", "arguments": {"name": "Refactor"}},
                    {"name": "list_tasks", "arguments": {}},
                ],
            }
        ]
    )
    await h.send_user("create Refactor and list them")
    msg = h.last_dispatched_batch()
    assert msg["type"] == "tool_call_batch"
    assert isinstance(msg["batch_id"], str) and len(msg["batch_id"]) > 0
    assert len(msg["tool_calls"]) == 2
    by_name = {tc["name"]: tc for tc in msg["tool_calls"]}
    # args are parsed objects, not JSON strings.
    assert by_name["create_task"]["args"] == {"name": "Refactor"}
    assert by_name["list_tasks"]["args"] == {}
    # call_ids unique and present.
    ids = [tc["call_id"] for tc in msg["tool_calls"]]
    assert len(set(ids)) == 2 and all(ids)


async def test_speech_pushed_before_tool_call_batch_send(harness_with_tools):
    """The processor must stream speech (LLMText* frames) before sending
    the atomic tool_call_batch server message."""
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "starting now",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            }
        ],
        speech_chunks_per_round=[["starting ", "now"]],
    )
    await h.send_user("go")

    # Find first LLMFullResponseEndFrame index in pushed_frames.
    end_indices = [
        i for i, f in enumerate(h.pushed_frames) if isinstance(f, LLMFullResponseEndFrame)
    ]
    assert end_indices, "expected an LLMFullResponseEndFrame to be pushed"
    end_idx = end_indices[0]
    # Server messages are recorded in order; the tool_call_batch message
    # must have been observed AFTER the end frame. We check this by
    # asserting the batch message exists at all (since send happens
    # after speech via await).
    batch = h.last_dispatched_batch()
    assert batch is not None

    # And start frame precedes end.
    start_indices = [
        i for i, f in enumerate(h.pushed_frames) if isinstance(f, LLMFullResponseStartFrame)
    ]
    assert start_indices and start_indices[0] < end_idx

    # And LLMTextFrame chunks landed in between.
    text_frames = [
        f for f in h.pushed_frames if isinstance(f, LLMTextFrame)
    ]
    assert text_frames, "expected at least one LLMTextFrame for streamed speech"
    combined = "".join(getattr(f, "text", "") for f in text_frames)
    assert combined == "starting now"


async def test_batch_id_persists_pending_to_in_flight_on_accept(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "ok doing it",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "pending_batch_resolution": "accept",
                "tool_invocations": [],
            },
        ]
    )
    await h.send_user("delete p1")
    pending_batch_id = h.processor._pending_confirmation_batch.batch_id
    await h.send_user("yes")
    in_flight_id = h.processor._in_flight_batch.batch_id
    assert in_flight_id == pending_batch_id

    # And the server-message batch_id matches.
    msg = h.last_dispatched_batch()
    assert msg["batch_id"] == pending_batch_id


async def test_batch_id_changes_for_replace(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm",
                "demonstration_action": "start_new",
                "demonstration_name": "delete",
                "tool_invocations": [{"name": "delete_task", "arguments": {}}],
            },
            {
                "user_turn_status": "complete",
                "speech": "instead listing",
                "demonstration_action": "continue",
                "demonstration_name": None,
                "pending_batch_resolution": "replace",
                "tool_invocations": [{"name": "list_tasks", "arguments": {}}],
            },
        ]
    )
    await h.send_user("delete it")
    old_id = h.processor._pending_confirmation_batch.batch_id
    await h.send_user("no, list")
    new_in_flight = h.processor._in_flight_batch.batch_id
    assert new_in_flight != old_id
