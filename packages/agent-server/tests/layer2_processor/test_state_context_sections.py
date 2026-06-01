"""The per-round state-context block is composed from sections, each
guarded by its own precondition. Verifies the conditional-prompting
contract: state determines content, no waste."""

from __future__ import annotations

from tests.harness.processor_harness import (
    ProcessorHarness,
    make_runtime_config,
    tool,
)


async def test_no_demo_no_history_block(harness):
    """No active demo → no batch history section."""
    msg = harness.processor._build_state_context_message(wake_mode="user_voice")
    assert "No active demonstration" in msg
    assert "Batch-by-batch history" not in msg


async def test_user_wake_idle_includes_action_block_only(harness_with_tools):
    """Idle state on user wake — action enum present; no pending /
    interrupt / turn-completeness blocks unless conditions match."""
    h = harness_with_tools
    msg = h.processor._build_state_context_message(wake_mode="user_text")
    # No demo active
    assert "No active demonstration" in msg
    # Allowed-actions block present with all three values
    assert "How to act this turn" in msg
    # No pending block (not pending state)
    assert "Batch pending confirmation" not in msg
    # No interrupt block (not in_flight)
    assert "Visitor spoke mid-demonstration" not in msg
    # No turn-completeness (text wake, not voice)
    assert "Turn completeness" not in msg


async def test_voice_wake_includes_turn_completeness_block(harness_with_tools):
    h = harness_with_tools
    msg = h.processor._build_state_context_message(wake_mode="user_voice")
    assert "Turn completeness" in msg
    assert "incomplete_short" in msg


async def test_text_wake_does_not_include_turn_completeness_block(harness_with_tools):
    h = harness_with_tools
    msg = h.processor._build_state_context_message(wake_mode="user_text")
    assert "Turn completeness" not in msg


async def test_active_demo_user_wake_includes_mid_demo_guidance(harness_with_tools):
    """When the visitor speaks while a demonstration is active, the
    mid-demonstration guidance block fires (regardless of whether a
    batch happens to be in flight)."""
    h = harness_with_tools
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
    await h.send_user("show me")
    msg = h.processor._build_state_context_message(wake_mode="user_voice")
    assert "Visitor spoke mid-demonstration" in msg
    assert "ambiguous" in msg.lower()


async def test_pending_state_includes_pending_block(harness_with_tools):
    h = harness_with_tools
    h.script_llm_outputs(
        [
            {
                "user_turn_status": "complete",
                "speech": "delete? confirm",
                "demonstration_action": "start_new",
                "demonstration_name": "x",
                "tool_invocations": [{"name": "delete_task", "arguments": {"id": "p1"}}],
            }
        ]
    )
    await h.send_user("delete it")
    msg = h.processor._build_state_context_message(wake_mode="user_voice")
    assert "Batch pending confirmation" in msg
    assert "delete_task" in msg
    assert "REQUIRES CONFIRMATION" in msg


async def test_no_tools_registered_no_tool_invocations_block():
    """Agent with no tools registered → tool_invocations not in schema
    on any wake → tool-invocation prose section omitted from
    state-context. Confirmable-tools block also omitted."""
    h = ProcessorHarness(runtime_config=make_runtime_config(tools=[]))
    try:
        for mode in ("user_voice", "user_text", "tool_batch_completed", "system"):
            msg = h.processor._build_state_context_message(wake_mode=mode)
            assert "Tool invocations" not in msg, f"wake_mode={mode}"
            assert "Confirmable tools" not in msg, f"wake_mode={mode}"
    finally:
        await h.shutdown()


async def test_no_confirmable_tools_no_confirmable_block():
    """If no registered tool has requires_confirmation=true, the
    CONFIRMABLE TOOLS reminder is omitted even when tool_invocations
    is in the schema."""
    cfg = make_runtime_config(tools=[
        tool("plain_a"),
        tool("plain_b"),
    ])
    h = ProcessorHarness(runtime_config=cfg)
    try:
        msg = h.processor._build_state_context_message(wake_mode="user_voice")
        assert "Tool invocations" in msg  # tool prose still there
        assert "Confirmable tools" not in msg  # but confirmable reminder gone
    finally:
        await h.shutdown()


async def test_confirmable_tool_present_block_renders():
    cfg = make_runtime_config(tools=[
        tool("plain"),
        tool("danger", requires_confirmation=True),
    ])
    h = ProcessorHarness(runtime_config=cfg)
    try:
        msg = h.processor._build_state_context_message(wake_mode="user_voice")
        assert "Confirmable tools" in msg
    finally:
        await h.shutdown()


async def test_budget_warning_only_near_ceiling(harness_with_tools):
    """Budget warning fires only when batches dispatched is at
    ceiling - 1 (i.e. one batch away from the cap)."""

    h = harness_with_tools
    h.set_max_batches_per_demo(3)
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
    await h.send_user("go")
    # 1 of 3 dispatched — no warning yet (below ceiling - 1).
    msg = h.processor._build_state_context_message(wake_mode="user_voice")
    assert "Budget warning" not in msg
    # Force counter to ceiling - 1.
    h.processor._active_demonstration.tool_batches_dispatched = 2
    msg = h.processor._build_state_context_message(wake_mode="user_voice")
    assert "Budget warning" in msg


async def test_action_enum_prose_matches_schema_enum(harness_with_tools):
    """The action prose lists only values that the schema for this
    wake actually contains. System wakes are skipped — they're
    speech-only and don't expose demonstration_action at all."""
    from brain.agent_output import build_in_app_schema

    h = harness_with_tools
    cases = [
        ("user_voice", {"continue", "start_new", "end_current"}),
        ("user_text", {"continue", "start_new", "end_current"}),
        ("tool_batch_completed", {"continue", "end_current"}),
    ]
    for wake, expected_set in cases:
        msg = h.processor._build_state_context_message(wake_mode=wake)
        for value in {"continue", "start_new", "end_current"}:
            schema_enum = build_in_app_schema(
                wake_mode=wake, batch_state="idle", tools=h.config.tools
            )["properties"]["demonstration_action"]["enum"]
            in_enum = value in schema_enum
            # The template references each enum value via either
            # backticks, the `*` bullet form, or the `'value'` quoted
            # form (e.g. system wake just says
            # "Set `demonstration_action='continue'`").
            in_prose = (
                f"`{value}`" in msg
                or f"* {value}" in msg
                or f"'{value}'" in msg
            )
            assert in_enum == (value in expected_set), wake
            if in_enum:
                assert in_prose, f"wake={wake} missing {value} in prose"
            else:
                # Values NOT in this wake's enum should also NOT appear
                # in the action-prose block — the template doesn't
                # meta-explain absences.
                actions_anchor = "How to act this turn"
                if actions_anchor in msg:
                    actions_idx = msg.index(actions_anchor)
                    actions_block = msg[actions_idx : actions_idx + 1000]
                    # Only check inside the action block; full template
                    # may mention the value elsewhere (e.g. interrupt
                    # guidance which is separately gated).
                    assert value not in actions_block, (
                        f"wake={wake} should not mention {value} in How-to-act block"
                    )
