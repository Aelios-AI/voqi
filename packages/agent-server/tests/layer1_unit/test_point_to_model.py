"""``InAppPointTo`` — the cursor-coordinate Pydantic model the
guide-mode LLM emits. Validates that x/y are clamped to [0, 1] and
that label length is bounded so the on-screen tag stays readable."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from brain.agent_output import InAppPointTo


def test_point_to_accepts_normalized_coords():
    p = InAppPointTo(x=0.5, y=0.25, label="Click here")
    assert p.x == 0.5 and p.y == 0.25
    assert p.label == "Click here"


@pytest.mark.parametrize("x", [0.0, 1.0])
@pytest.mark.parametrize("y", [0.0, 1.0])
def test_point_to_accepts_edges(x, y):
    InAppPointTo(x=x, y=y, label="edge")


@pytest.mark.parametrize(
    "bad_x,bad_y",
    [
        (-0.01, 0.5),  # left of left edge
        (1.01, 0.5),   # right of right edge
        (0.5, -0.01),  # above top edge
        (0.5, 1.01),   # below bottom edge
    ],
)
def test_point_to_rejects_out_of_range(bad_x, bad_y):
    with pytest.raises(ValidationError):
        InAppPointTo(x=bad_x, y=bad_y, label="oops")


def test_point_to_label_max_length_enforced():
    """The label is meant to be a short on-screen tag, not an essay.
    The 80-char ceiling is generous (e.g. localized 'Open Settings'
    fits with margin) but keeps the cursor bubble from blowing out."""
    # 80 chars exactly is fine.
    InAppPointTo(x=0.5, y=0.5, label="x" * 80)
    # 81 fails.
    with pytest.raises(ValidationError):
        InAppPointTo(x=0.5, y=0.5, label="x" * 81)
