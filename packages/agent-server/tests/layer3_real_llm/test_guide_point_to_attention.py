"""Layer 3: prove the real LLM in guide mode actually grounds its
``point_to`` coordinates in the screenshot it sees.

Direct (not LLM-judge) assertion. We render a synthetic screenshot
with a single, visually unambiguous button in a known region of the
image and ask the agent in guide mode "where do I click to start?".
The agent should set ``point_to`` whose normalized x/y land inside
the button's bounding box.

If the contract is broken — image not attached at the human marker,
the LLM not attending to it, the schema not exposing point_to in
guide mode — the model can't possibly land within the box because
the button location is uniquely determined by the image bytes we
just rendered.

This complements ``test_screenshot_attention.py`` (which proves the
LLM reads TEXT from a screenshot in action mode) by proving it can
SPATIALLY ground a coordinate in guide mode.

Run with:  pytest tests/layer3_real_llm -m llm_judge
"""

from __future__ import annotations

import base64
import io
import os

import pytest

from brain.screenshot_service import CapturedScreenshot
from tests.harness.processor_harness import make_runtime_config

pytestmark = pytest.mark.llm_judge


REAL_AGENT_MODEL = os.getenv("IN_APP_LLM_MODEL_FOR_TESTS", "gpt-5.4")


# Image dimensions + button bounds. The button is drawn as a single
# bright filled rectangle on a neutral background — no other UI
# elements compete for attention, so the LLM has exactly one place
# to point.
_IMG_W = 1024
_IMG_H = 600

# Button bounds (left, top, right, bottom) in pixel coords. Placed
# clearly off-centre so a "guess the middle" model fails — the
# coordinate must come from actually reading the image.
_BTN_LEFT = 760
_BTN_TOP = 80
_BTN_RIGHT = 980
_BTN_BOTTOM = 160


def _make_button_screenshot() -> CapturedScreenshot:
    """Render a single button at the top-right of a blank canvas."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (_IMG_W, _IMG_H), color=(245, 246, 250))
    draw = ImageDraw.Draw(img)

    # Try platform fonts; fall back to default.
    btn_font = None
    label_font = None
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            btn_font = ImageFont.truetype(candidate, 24)
            label_font = ImageFont.truetype(candidate, 28)
            break
        except OSError:
            continue
    if btn_font is None:
        btn_font = ImageFont.load_default()
        label_font = btn_font

    # Lots of negative space below the header — there's literally
    # nothing else for the LLM to point at.
    draw.text(
        (40, 30), "TestApp — Task dashboard",
        fill=(20, 24, 32), font=label_font,
    )
    # The single button.
    draw.rounded_rectangle(
        (_BTN_LEFT, _BTN_TOP, _BTN_RIGHT, _BTN_BOTTOM),
        radius=12,
        fill=(59, 130, 246),
        outline=(40, 90, 180),
        width=2,
    )
    draw.text(
        (_BTN_LEFT + 24, _BTN_TOP + 24),
        "Add task",
        fill=(255, 255, 255),
        font=btn_font,
    )

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    image_bytes = buf.getvalue()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    return CapturedScreenshot(
        image_b64=image_b64,
        mime="image/jpeg",
        width=_IMG_W,
        height=_IMG_H,
        request_id="test-guide-pointing",
        elapsed_ms=0.0,
    )


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="real-LLM test needs OPENAI_API_KEY",
)
async def test_guide_mode_point_to_lands_inside_target_button():
    """End-to-end: real LLM in guide mode + real button screenshot
    + 'where do I click?' question = point_to coordinates inside
    the rendered button's bounding box."""
    from langchain_openai import ChatOpenAI

    from tests.harness.processor_harness import ProcessorHarness

    cfg = make_runtime_config(
        tools=[],
        software_name="TestApp",
        software_tldr=(
            "TestApp is a task-management dashboard. "
            "Visitors create tasks from the dashboard's main view."
        ),
        mode="guide",
    )
    h = ProcessorHarness(runtime_config=cfg)
    try:
        # Real LLM, not the harness's scripted fake.
        h.processor._llm = ChatOpenAI(
            model=REAL_AGENT_MODEL,
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.2,
        )
        # Inject the synthetic button image as the next captured
        # screenshot. Default placeholder gets replaced.
        h.screenshots.script_capture(_make_button_screenshot())

        await h.send_user("How do I add a new task?")

        # Find the screenshot_result round (the one that actually
        # ran the LLM in guide mode).
        cursor_msgs = h.rtvi.server_messages_of_type("guide_cursor")
        assert cursor_msgs, (
            "Guide mode round should have dispatched a guide_cursor "
            "server message — the LLM saw a single 'Add task' button "
            "in the screenshot and was asked exactly where to click."
        )
        last = cursor_msgs[-1]

        x_px = last["x"] * _IMG_W
        y_px = last["y"] * _IMG_H

        # Allow a generous tolerance — we don't need pixel-perfect, we
        # just need the LLM to be in the right neighbourhood. The
        # button is 220×80 px in a 1024×600 image; landing anywhere
        # inside (with a 24px slack outside) means the LLM grounded
        # its answer in the image rather than guessing.
        slack = 24
        assert _BTN_LEFT - slack <= x_px <= _BTN_RIGHT + slack, (
            f"point_to.x={last['x']:.3f} ({x_px:.0f}px) is outside the "
            f"'Add task' button x-range "
            f"[{_BTN_LEFT - slack}, {_BTN_RIGHT + slack}]px — LLM did "
            f"not ground its coordinate in the screenshot"
        )
        assert _BTN_TOP - slack <= y_px <= _BTN_BOTTOM + slack, (
            f"point_to.y={last['y']:.3f} ({y_px:.0f}px) is outside the "
            f"'Add task' button y-range "
            f"[{_BTN_TOP - slack}, {_BTN_BOTTOM + slack}]px — LLM did "
            f"not ground its coordinate in the screenshot"
        )
    finally:
        await h.shutdown()
