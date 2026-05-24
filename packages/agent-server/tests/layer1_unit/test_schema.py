"""build_in_app_schema shape gating: the LLM literally cannot emit
fields that don't apply this round."""

from __future__ import annotations

import pytest

from brain.agent_output import build_in_app_schema
from brain.config import InAppTool


def _tools() -> list[InAppTool]:
    return [
        InAppTool(name="t1", description="d1", parameters={}),
        InAppTool(name="t2", description="d2", parameters={}, requires_confirmation=True),
    ]


# ── is_message_relevant visibility (relevance gate, voice-only) ───────


def test_is_message_relevant_present_only_on_user_voice():
    s = build_in_app_schema(wake_mode="user_voice", batch_state="idle", tools=_tools())
    assert "is_message_relevant" in s["properties"]
    assert "is_message_relevant" in s["required"]
    assert s["properties"]["is_message_relevant"]["enum"] == ["relevant", "off_topic"]


@pytest.mark.parametrize("mode", ["user_text", "tool_batch_completed", "system"])
def test_is_message_relevant_absent_on_other_wakes(mode):
    """Text wakes are always-valid by construction; tool / system wakes
    have no visitor utterance to classify."""
    s = build_in_app_schema(wake_mode=mode, batch_state="idle", tools=_tools())
    assert "is_message_relevant" not in s["properties"]
    assert "is_message_relevant" not in s["required"]


# ── idle_warning_resolution visibility (stage-2 grace gate) ───────────


def test_idle_warning_resolution_absent_when_stage_two_not_armed():
    """The default — no stage-2 grace running. Field must not appear
    on any wake (otherwise the LLM might emit it on irrelevant turns)."""
    for mode in ("user_voice", "user_text", "tool_batch_completed", "system"):
        s = build_in_app_schema(
            wake_mode=mode, batch_state="idle", tools=_tools(),
            idle_stage_two_armed=False,
        )
        assert "idle_warning_resolution" not in s["properties"], mode
        assert "idle_warning_resolution" not in s["required"], mode


@pytest.mark.parametrize("mode", ["user_voice", "user_text"])
def test_idle_warning_resolution_present_on_user_wake_when_armed(mode):
    """Stage 2 armed + visitor spoke / typed → field is required this
    turn so the LLM must classify (end_session vs continue_session)."""
    s = build_in_app_schema(
        wake_mode=mode, batch_state="idle", tools=_tools(),
        idle_stage_two_armed=True,
    )
    assert "idle_warning_resolution" in s["properties"]
    assert "idle_warning_resolution" in s["required"]
    assert s["properties"]["idle_warning_resolution"]["enum"] == [
        "end_session", "continue_session",
    ]


@pytest.mark.parametrize("mode", ["tool_batch_completed", "system"])
def test_idle_warning_resolution_absent_on_non_user_wake_even_if_armed(mode):
    """Tool / system wakes don't represent the visitor responding to
    the check-in. The grace task keeps ticking; the field doesn't
    apply, schema must not expose it."""
    s = build_in_app_schema(
        wake_mode=mode, batch_state="idle", tools=_tools(),
        idle_stage_two_armed=True,
    )
    assert "idle_warning_resolution" not in s["properties"]
    assert "idle_warning_resolution" not in s["required"]


# ── user_turn_status visibility ────────────────────────────────────────


def test_user_turn_status_present_only_on_user_voice():
    s = build_in_app_schema(wake_mode="user_voice", batch_state="idle", tools=_tools())
    assert "user_turn_status" in s["properties"]
    assert "user_turn_status" in s["required"]


@pytest.mark.parametrize("mode", ["user_text", "tool_batch_completed", "system"])
def test_user_turn_status_absent_on_other_wakes(mode):
    s = build_in_app_schema(wake_mode=mode, batch_state="idle", tools=_tools())
    assert "user_turn_status" not in s["properties"]
    assert "user_turn_status" not in s["required"]


# ── pending_batch_resolution visibility ────────────────────────────────


# System wakes are speech-only nudges; no other field — including
# pending_batch_resolution — appears regardless of batch_state.
@pytest.mark.parametrize("mode", ["user_voice", "user_text", "tool_batch_completed"])
def test_pending_resolution_only_when_pending_state(mode):
    idle = build_in_app_schema(wake_mode=mode, batch_state="idle", tools=_tools())
    assert "pending_batch_resolution" not in idle["properties"]
    in_flight = build_in_app_schema(wake_mode=mode, batch_state="in_flight", tools=_tools())
    assert "pending_batch_resolution" not in in_flight["properties"]
    pending = build_in_app_schema(
        wake_mode=mode, batch_state="pending_confirmation", tools=_tools()
    )
    assert "pending_batch_resolution" in pending["properties"]
    assert "pending_batch_resolution" in pending["required"]
    # The resolution enum MUST NOT include 'decline' — declining
    # without a replacement would leave the demo stuck.
    enum = pending["properties"]["pending_batch_resolution"]["enum"]
    assert sorted(enum) == sorted(["accept", "replace", "keep_waiting"])
    assert "decline" not in enum


# ── tool_calls visibility (two-trigger rule) ───────────────────────────


def test_tool_calls_visible_on_user_input_idle():
    for mode in ("user_voice", "user_text"):
        s = build_in_app_schema(wake_mode=mode, batch_state="idle", tools=_tools())
        assert "tool_invocations" in s["properties"]


def test_tool_calls_visible_on_tool_batch_completed():
    s = build_in_app_schema(
        wake_mode="tool_batch_completed", batch_state="in_flight", tools=_tools()
    )
    assert "tool_invocations" in s["properties"]


def test_tool_calls_visible_on_pending_confirmation_for_replace():
    s = build_in_app_schema(
        wake_mode="user_voice", batch_state="pending_confirmation", tools=_tools()
    )
    assert "tool_invocations" in s["properties"]


def test_tool_calls_hidden_when_no_tools_registered():
    s = build_in_app_schema(wake_mode="user_voice", batch_state="idle", tools=[])
    assert "tool_invocations" not in s["properties"]


def test_tool_calls_enum_lists_registered_tool_names_only():
    s = build_in_app_schema(wake_mode="user_voice", batch_state="idle", tools=_tools())
    enum = s["properties"]["tool_invocations"]["items"]["properties"]["name"]["enum"]
    assert sorted(enum) == ["t1", "t2"]


# ── always-required fields ─────────────────────────────────────────────


# speech + demonstration_action are required on every wake EXCEPT
# system (system is speech-only — see test_system_wake_is_speech_only).
@pytest.mark.parametrize("mode", ["user_voice", "user_text", "tool_batch_completed"])
@pytest.mark.parametrize("state", ["idle", "in_flight", "pending_confirmation"])
def test_speech_and_action_always_required(mode, state):
    s = build_in_app_schema(wake_mode=mode, batch_state=state, tools=_tools())
    for field in ("speech", "demonstration_action"):
        assert field in s["properties"]
        assert field in s["required"]


@pytest.mark.parametrize("state", ["idle", "in_flight", "pending_confirmation"])
def test_demonstration_name_required_only_when_start_new_allowed(state):
    """`demonstration_name` is in the schema only on wakes where the
    `start_new` action is in the enum (user_voice / user_text). On
    tool_batch_completed and system wakes, start_new doesn't exist so
    demonstration_name has no purpose and is omitted from the schema."""
    for mode in ("user_voice", "user_text"):
        s = build_in_app_schema(wake_mode=mode, batch_state=state, tools=_tools())
        assert "demonstration_name" in s["properties"]
        assert "demonstration_name" in s["required"]
    s = build_in_app_schema(
        wake_mode="tool_batch_completed", batch_state=state, tools=_tools(),
    )
    assert "demonstration_name" not in s["properties"]
    assert "demonstration_name" not in s["required"]


def test_demonstration_action_enum_per_wake():
    """Per-wake action enum: user wakes carry all three values;
    tool_batch_completed strips start_new (no fresh user signal)."""
    user_voice = build_in_app_schema(wake_mode="user_voice", batch_state="idle", tools=[])
    assert sorted(user_voice["properties"]["demonstration_action"]["enum"]) == [
        "continue", "end_current", "start_new",
    ]
    user_text = build_in_app_schema(wake_mode="user_text", batch_state="idle", tools=[])
    assert sorted(user_text["properties"]["demonstration_action"]["enum"]) == [
        "continue", "end_current", "start_new",
    ]
    tbc = build_in_app_schema(wake_mode="tool_batch_completed", batch_state="in_flight", tools=[])
    assert sorted(tbc["properties"]["demonstration_action"]["enum"]) == [
        "continue", "end_current",
    ]
