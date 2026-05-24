"""Unit tests for the screenshot broker + the multimodal message
helper that splices the captured bytes onto the human-marker.

The processor wires these together; the tests here exercise each
piece in isolation:

* ``ScreenshotService.request`` round-trips a request_id through an
  injected sender, awaits the matching ``resolve``, returns the
  decoded ``CapturedScreenshot`` (or ``None`` on timeout).
* Late / unmatched / malformed responses are dropped silently and the
  awaiter reaches its timeout.
* ``_build_inference_messages`` produces a plain string human marker
  when no capture is provided, and a multimodal content-block list
  with the JPEG ``image_url`` when one is.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from brain.processor import _build_inference_messages
from brain.screenshot_service import (
    CapturedScreenshot,
    ScreenshotService,
)

PIXEL_B64 = base64.b64encode(b"\xff\xd8\xff\xd9").decode()  # tiny "JPEG"


def _make_sender(captured: list[dict]):
    async def sender(payload: dict) -> None:
        captured.append(payload)
    return sender


# ── ScreenshotService.request → resolve happy path ──────────────────


@pytest.mark.asyncio
async def test_request_resolves_with_widget_payload():
    sent: list[dict] = []
    svc = ScreenshotService(sender=_make_sender(sent), default_timeout_seconds=2.0)

    async def respond_after_send():
        # Wait until the request has been sent so we know its request_id.
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.001)
        assert sent, "service never called sender"
        request_id = sent[0]["request_id"]
        svc.resolve(
            {
                "request_id": request_id,
                "image_b64": PIXEL_B64,
                "mime": "image/jpeg",
                "width": 1280,
                "height": 720,
            }
        )

    responder = asyncio.create_task(respond_after_send())
    captured = await svc.request()
    await responder

    assert isinstance(captured, CapturedScreenshot)
    assert captured.image_b64 == PIXEL_B64
    assert captured.mime == "image/jpeg"
    assert captured.width == 1280
    assert captured.height == 720
    assert sent[0]["type"] == "request_screenshot"
    assert svc.pending_count == 0


@pytest.mark.asyncio
async def test_request_returns_none_on_timeout():
    """Widget never replies — request honours the per-call timeout and
    falls back to text-only by returning None."""
    svc = ScreenshotService(
        sender=_make_sender([]), default_timeout_seconds=0.05
    )
    captured = await svc.request()
    assert captured is None
    assert svc.pending_count == 0


@pytest.mark.asyncio
async def test_request_returns_none_on_widget_error_payload():
    """Widget reported a capture failure (CSP, html2canvas threw).
    We must NOT block on the rest of the budget — resolve immediately
    with None so the inference proceeds text-only."""
    sent: list[dict] = []
    svc = ScreenshotService(sender=_make_sender(sent), default_timeout_seconds=2.0)

    async def respond_with_error():
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.001)
        svc.resolve(
            {"request_id": sent[0]["request_id"], "error": "CSP refused canvas read"}
        )

    err_task = asyncio.create_task(respond_with_error())
    captured = await svc.request()
    await err_task
    assert captured is None


# ── tolerant resolve() against junk payloads ────────────────────────


@pytest.mark.asyncio
async def test_resolve_drops_unknown_request_id():
    """A late response after the awaiter timed out must NOT crash."""
    svc = ScreenshotService(sender=_make_sender([]), default_timeout_seconds=2.0)
    svc.resolve({"request_id": "never-issued", "image_b64": PIXEL_B64})
    # No awaiter to satisfy; just confirm it didn't raise.
    assert svc.pending_count == 0


@pytest.mark.asyncio
async def test_resolve_drops_response_without_request_id():
    sent: list[dict] = []
    svc = ScreenshotService(sender=_make_sender(sent), default_timeout_seconds=0.05)
    svc.resolve({"image_b64": PIXEL_B64})  # missing request_id
    captured = await svc.request()  # resolves to None via timeout
    assert captured is None


@pytest.mark.asyncio
async def test_resolve_drops_invalid_base64():
    sent: list[dict] = []
    svc = ScreenshotService(sender=_make_sender(sent), default_timeout_seconds=2.0)

    async def respond_garbage():
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.001)
        svc.resolve(
            {"request_id": sent[0]["request_id"], "image_b64": "@@@not base64@@@"}
        )

    task = asyncio.create_task(respond_garbage())
    captured = await svc.request()
    await task
    assert captured is None


# ── cancel_all releases pending awaiters ─────────────────────────────


@pytest.mark.asyncio
async def test_cancel_all_resolves_pending_awaiters():
    sent: list[dict] = []
    svc = ScreenshotService(sender=_make_sender(sent), default_timeout_seconds=10.0)

    async def cancel_after_send():
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.001)
        svc.cancel_all()

    task = asyncio.create_task(cancel_after_send())
    captured = await svc.request()
    await task
    assert captured is None
    assert svc.pending_count == 0


# ── _build_inference_messages: text-only vs multimodal ──────────────


def test_build_messages_text_only_when_no_capture():
    msgs = _build_inference_messages(system_prompt="SYS", captured=None)
    assert msgs[0] == ("system", "SYS")
    role, content = msgs[1]
    assert role == "human"
    assert isinstance(content, str)
    assert content  # non-empty marker


def test_build_messages_multimodal_when_capture_present():
    captured = CapturedScreenshot(
        image_b64=PIXEL_B64,
        mime="image/jpeg",
        width=1280,
        height=720,
        request_id="abc",
        elapsed_ms=12.0,
    )
    msgs = _build_inference_messages(system_prompt="SYS", captured=captured)
    assert msgs[0] == ("system", "SYS")
    role, content = msgs[1]
    assert role == "human"
    assert isinstance(content, list)
    assert len(content) == 2

    text_block = content[0]
    image_block = content[1]
    assert text_block["type"] == "text"
    assert text_block["text"]  # human marker is non-empty

    assert image_block["type"] == "image_url"
    assert "image_url" in image_block
    url = image_block["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert url.endswith(PIXEL_B64)


def test_build_messages_uses_captured_mime():
    captured = CapturedScreenshot(
        image_b64=PIXEL_B64,
        mime="image/png",
        width=320,
        height=180,
        request_id="abc",
        elapsed_ms=0.0,
    )
    msgs = _build_inference_messages(system_prompt="X", captured=captured)
    _, content = msgs[1]
    assert isinstance(content, list)
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
