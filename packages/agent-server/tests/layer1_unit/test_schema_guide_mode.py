"""Guide-mode schema gating: ``mode="guide"`` produces a tight schema
with ``speech`` + ``point_to`` (always) plus voice-originated gates
(relevance + completeness) when the wake actually represents voice
input. Action-mode-only fields (tool_invocations, demonstration_action,
demonstration_name, pending_batch_resolution, decision_to_request_
screenshot, screenshot_request_context) MUST be absent.
"""

from __future__ import annotations

import pytest

from brain.agent_output import build_in_app_schema
from brain.config import InAppTool


def _tools() -> list[InAppTool]:
    return [
        InAppTool(name="t1", description="d1", parameters={}),
        InAppTool(name="t2", description="d2", parameters={}, requires_confirmation=True),
    ]


# ── Action-mode-only fields are absent in guide mode ─────────────────


@pytest.mark.parametrize(
    "wake_mode",
    ["user_voice", "user_text", "screenshot_result", "tool_batch_completed"],
)
def test_guide_mode_omits_tool_invocations_even_with_tools(wake_mode):
    """No matter how many tools are registered server-side, guide mode
    NEVER exposes tool_invocations. The visitor picked guide explicitly,
    the bot's tool list is dropped, and the LLM should not be tempted
    to think about tool calls."""
    s = build_in_app_schema(
        wake_mode=wake_mode, batch_state="idle", tools=_tools(), mode="guide",
    )
    assert "tool_invocations" not in s["properties"]
    assert "tool_invocations" not in s["required"]


@pytest.mark.parametrize(
    "wake_mode",
    ["user_voice", "user_text", "screenshot_result", "tool_batch_completed"],
)
def test_guide_mode_omits_demonstration_fields(wake_mode):
    """Demonstrations are an action-mode concept (multi-batch tool
    flows). Guide mode has no demos, so demonstration_action +
    demonstration_name must be omitted."""
    s = build_in_app_schema(
        wake_mode=wake_mode, batch_state="idle", tools=_tools(), mode="guide",
    )
    assert "demonstration_action" not in s["properties"]
    assert "demonstration_name" not in s["properties"]


@pytest.mark.parametrize(
    "batch_state", ["idle", "in_flight", "pending_confirmation"],
)
def test_guide_mode_omits_pending_batch_resolution(batch_state):
    """Even if a stale batch_state is somehow passed, guide mode never
    runs tool batches so pending_batch_resolution is never relevant."""
    s = build_in_app_schema(
        wake_mode="screenshot_result",
        batch_state=batch_state,
        tools=_tools(),
        mode="guide",
    )
    assert "pending_batch_resolution" not in s["properties"]


@pytest.mark.parametrize("wake_mode", ["user_voice", "user_text"])
def test_guide_mode_omits_screenshot_decision_fields(wake_mode):
    """The processor force-fetches a screenshot on every user wake in
    guide mode — the LLM never decides. So decision_to_request_
    screenshot + screenshot_request_context must be absent."""
    s = build_in_app_schema(
        wake_mode=wake_mode, batch_state="idle", tools=_tools(), mode="guide",
    )
    assert "decision_to_request_screenshot" not in s["properties"]
    assert "screenshot_request_context" not in s["properties"]


# ── point_to: present on every non-kickoff guide round ───────────────


@pytest.mark.parametrize(
    "wake_mode",
    ["user_voice", "user_text", "screenshot_result", "tool_batch_completed"],
)
def test_guide_mode_exposes_point_to(wake_mode):
    s = build_in_app_schema(
        wake_mode=wake_mode, batch_state="idle", tools=_tools(), mode="guide",
    )
    assert "point_to" in s["properties"]
    # Required so the LLM has to make an explicit decision (null when
    # there's nothing to point at, object when pointing); the schema
    # accepts both via the union type.
    assert "point_to" in s["required"]
    point_to = s["properties"]["point_to"]
    assert point_to["type"] == ["object", "null"]
    # Inner shape must enforce normalized [0, 1] coords + label.
    inner_props = point_to["properties"]
    assert set(inner_props.keys()) == {"x", "y", "label"}
    assert inner_props["x"]["minimum"] == 0.0
    assert inner_props["x"]["maximum"] == 1.0
    assert inner_props["y"]["minimum"] == 0.0
    assert inner_props["y"]["maximum"] == 1.0
    assert sorted(point_to["required"]) == ["label", "x", "y"]


# ── Kickoff stays speech-only in guide mode (same as action) ─────────


def test_guide_mode_kickoff_is_speech_only():
    """Kickoff fires before the visitor has spoken — no screenshot
    yet, no question to answer. Both modes must short-circuit to a
    speech-only schema. Guide mode shares the same kickoff behaviour
    via the early kickoff branch in build_in_app_schema."""
    s = build_in_app_schema(
        wake_mode="kickoff", batch_state="idle", tools=_tools(), mode="guide",
    )
    assert set(s["properties"].keys()) == {"speech"}
    assert s["required"] == ["speech"]


# ── Voice gates: only when the LLM is reasoning over a voice turn ────


def test_guide_mode_voice_gates_present_on_user_voice_wake():
    """user_voice wake in guide mode: schema is the agent's first
    inference on the visitor's audio. Mic is open passively, so
    is_message_relevant + user_turn_status apply just like action
    mode."""
    s = build_in_app_schema(
        wake_mode="user_voice", batch_state="idle", tools=_tools(), mode="guide",
    )
    assert "is_message_relevant" in s["properties"]
    assert "user_turn_status" in s["properties"]
    assert "is_message_relevant" in s["required"]
    assert "user_turn_status" in s["required"]


def test_guide_mode_voice_gates_present_on_screenshot_result_when_original_was_voice():
    """In guide mode the LLM's actual inference happens at
    screenshot_result wake (the user_voice wake itself is short-
    circuited into a screenshot fetch). When the underlying input
    was voice, the gates need to apply at this wake instead."""
    s = build_in_app_schema(
        wake_mode="screenshot_result",
        batch_state="idle",
        tools=_tools(),
        mode="guide",
        original_wake_mode="user_voice",
    )
    assert "is_message_relevant" in s["properties"]
    assert "user_turn_status" in s["properties"]


def test_guide_mode_voice_gates_absent_on_screenshot_result_when_original_was_text():
    """Typed text is always for-you and always complete, so the gates
    are noise on text-originated screenshot_result rounds."""
    s = build_in_app_schema(
        wake_mode="screenshot_result",
        batch_state="idle",
        tools=_tools(),
        mode="guide",
        original_wake_mode="user_text",
    )
    assert "is_message_relevant" not in s["properties"]
    assert "user_turn_status" not in s["properties"]


def test_guide_mode_voice_gates_absent_on_user_text_wake():
    s = build_in_app_schema(
        wake_mode="user_text", batch_state="idle", tools=_tools(), mode="guide",
    )
    assert "is_message_relevant" not in s["properties"]
    assert "user_turn_status" not in s["properties"]


# ── idle_warning_resolution carries over correctly ───────────────────


@pytest.mark.parametrize(
    "wake_mode", ["user_voice", "user_text", "screenshot_result"],
)
def test_guide_mode_idle_warning_resolution_when_armed(wake_mode):
    """Stage 2 grace period: visitor's reply must be classified
    end_session vs continue_session. Guide mode exposes the field on
    user-voice / user-text / screenshot_result wakes (the wakes that
    can carry the visitor's response)."""
    s = build_in_app_schema(
        wake_mode=wake_mode,
        batch_state="idle",
        tools=_tools(),
        mode="guide",
        idle_stage_two_armed=True,
    )
    assert "idle_warning_resolution" in s["properties"]
    assert s["properties"]["idle_warning_resolution"]["enum"] == [
        "end_session", "continue_session",
    ]


@pytest.mark.parametrize(
    "wake_mode", ["user_voice", "user_text", "screenshot_result"],
)
def test_guide_mode_idle_warning_resolution_absent_when_not_armed(wake_mode):
    s = build_in_app_schema(
        wake_mode=wake_mode,
        batch_state="idle",
        tools=_tools(),
        mode="guide",
        idle_stage_two_armed=False,
    )
    assert "idle_warning_resolution" not in s["properties"]


# ── speech is always required ────────────────────────────────────────


@pytest.mark.parametrize(
    "wake_mode",
    ["user_voice", "user_text", "screenshot_result", "tool_batch_completed"],
)
def test_guide_mode_speech_always_required(wake_mode):
    s = build_in_app_schema(
        wake_mode=wake_mode, batch_state="idle", tools=_tools(), mode="guide",
    )
    assert "speech" in s["properties"]
    assert "speech" in s["required"]
