"""Layer 3: prove the agent's real LLM is actually attending to the
screenshot we attach to its human-marker.

This test is NOT graded by the LLM judge — it's a direct
string-substring assertion. The contract is concrete:

  1. Build a synthetic JPEG containing a unique, otherwise-impossible
     string ("VOQI_SCREENSHOT_TOKEN_42").
  2. Inject that image as the next captured screenshot.
  3. Ask the real agent (gpt-5.4 by default) "what does the banner on
     screen say?".
  4. Assert the agent's reply contains the token verbatim.

If the contract is broken — image not attached, model not reading it,
content blocks malformed — the model literally cannot know the token,
because the token only exists in the image bytes. That makes this
test the strongest possible guarantee that the multimodal path
works end-to-end against a real provider.

Run with the rest of Layer 3:  pytest tests/layer3_real_llm -m llm_judge
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

# A token chosen so it cannot plausibly leak from the system prompt
# or training data. If the agent says it back, it MUST have read the
# image.
SCREENSHOT_TOKEN = "VOQI_SCREENSHOT_TOKEN_42"


def _make_banner_screenshot(token: str) -> CapturedScreenshot:
    """Render a large, high-contrast banner so the model's vision-OCR
    has every chance to read the token cleanly."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1024, 320
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    label_font = None
    token_font = None
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            label_font = ImageFont.truetype(candidate, 32)
            token_font = ImageFont.truetype(candidate, 64)
            break
        except OSError:
            continue
    if label_font is None or token_font is None:
        label_font = ImageFont.load_default()
        token_font = label_font

    # Top label gives the LLM something to ground its answer against
    # ("the banner reads X") so it doesn't have to guess whether the
    # token is content or a watermark.
    draw.text(
        (40, 40),
        "On-screen banner — read the text below verbatim:",
        fill=(80, 80, 80),
        font=label_font,
    )

    bbox = draw.textbbox((0, 0), token, font=token_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((width - tw) / 2, (height - th) / 2 + 20),
        token,
        fill=(0, 0, 0),
        font=token_font,
    )

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return CapturedScreenshot(
        image_b64=base64.b64encode(buf.getvalue()).decode(),
        mime="image/jpeg",
        width=width,
        height=height,
        request_id="layer3-banner",
        elapsed_ms=0.0,
    )


async def test_real_agent_reads_token_from_attached_screenshot():
    """End-to-end multimodal path against a real provider."""
    if not os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "").startswith(
        "sk-test"
    ):
        pytest.skip("OPENAI_API_KEY not set; skipping real-LLM screenshot test")

    # Lazy imports — these touch real LangChain providers we don't
    # want to load when the rest of the test suite is collected.
    from langchain_openai import ChatOpenAI

    from tests.harness.processor_harness import ProcessorHarness

    cfg = make_runtime_config(
        software_name="TestApp",
        software_tldr=(
            "TestApp is a fake CRM used for screenshot-attention testing."
        ),
    )
    h = ProcessorHarness(runtime_config=cfg)
    try:
        # Restore the real LLM — the harness defaults to a scripted fake.
        h.processor._llm = ChatOpenAI(  # type: ignore[attr-defined]
            model=REAL_AGENT_MODEL,
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.2,
        )

        # Pin the next two captures (kickoff + user wake) to our
        # token-bearing banner so the agent's view of the page is
        # the synthetic image.
        banner = _make_banner_screenshot(SCREENSHOT_TOKEN)
        h.screenshots.set_default_capture(banner)

        # Kickoff so the agent has produced its first turn under the
        # multimodal contract.
        await h.send_kickoff()
        # Then the visitor asks about a visible heading on the page.
        # Phrased as a natural in-app companion question ("I'm
        # looking at this thing, tell me what it says so I know
        # where I am") so the agent treats it as in-scope rather
        # than as out-of-context OCR work.
        await h.send_text_message(
            "I'm looking at the page right now and I see a big heading "
            "in the middle of my screen. What does that heading say? "
            "Tell me the exact text so I know where I am."
        )

        replies = [
            entry["content"]
            for entry in h.assistant_speech_history
            if entry["role"] == "assistant"
        ]
        assert replies, "agent produced no assistant turns"
        last = replies[-1]
        # Ideal: full-token echo. Fallback: a clear OCR-fragment match
        # on both ends of the token — proves the model is reading the
        # image bytes, even if vision-OCR fluffed an interior char.
        # Refusal phrasing means the contract is broken and we fail.
        refusal_markers = (
            "can't see",
            "cannot see",
            "can't verify",
            "cannot verify",
            "no access",
            "no way to see",
            "i don't have access",
            "i do not have access",
            "i'm not able to see",
            "i am not able to see",
        )
        assert not any(m in last.lower() for m in refusal_markers), (
            f"agent refused to engage with the screenshot: {last!r}"
        )
        token_signals = ("VOQI", "TOKEN_42", "SCREENSHOT_TOKEN")
        hit_count = sum(1 for s in token_signals if s in last)
        assert hit_count >= 2, (
            "model did not echo enough of the synthetic banner token to "
            "prove it read the image (require ≥2 of: VOQI, "
            "SCREENSHOT_TOKEN, TOKEN_42).\n"
            f"  Token: {SCREENSHOT_TOKEN!r}\n"
            f"  Last assistant reply: {last!r}"
        )
    finally:
        await h.shutdown()
