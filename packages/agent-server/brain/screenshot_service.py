"""Screenshot request/response broker.

Every LLM inference is multimodal: the agent's view of the visitor's
screen is captured by the widget (via html2canvas) and attached to the
human-marker message. This module is the asyncio-side waiting room
that maps a request_id we sent to the widget to the response that
eventually comes back over the RTVI client-message channel.

Lifecycle of one capture:

1. Processor calls :meth:`request` just before building the LLM
   messages. Service generates a request_id, registers an
   ``asyncio.Future`` for it, calls the injected sender callback with
   ``{"type": "request_screenshot", "request_id": ...}``, then awaits
   the future with a timeout.

2. Widget receives the message, runs html2canvas, posts back
   ``{"type": "screenshot_response", "request_id": ..., "image_b64":
   ..., "mime": "image/jpeg"}``. The processor's RTVI on_client_message
   handler calls :meth:`resolve`, which sets the future's result.

3. :meth:`request` returns the image bytes (or ``None`` on timeout /
   widget-side error).

If the widget never responds the future times out and we proceed
text-only — never block inference indefinitely on a screenshot.
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from loguru import logger

# Default per-request budget. The processor passes its own value but
# this is the sane fallback used by the unit tests.
DEFAULT_SCREENSHOT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class CapturedScreenshot:
    """Decoded image returned from a successful capture."""

    image_b64: str          # raw base64 (no data: prefix), ready to embed
    mime: str               # "image/jpeg" typically
    width: Optional[int]    # informational; widget reports if known
    height: Optional[int]
    request_id: str
    elapsed_ms: float       # round-trip time for telemetry / tests


class ScreenshotService:
    """Pairs outbound ``request_screenshot`` messages with their inbound
    ``screenshot_response`` payloads via per-request asyncio Futures."""

    def __init__(
        self,
        *,
        sender: Callable[[dict], Awaitable[None]],
        default_timeout_seconds: float = DEFAULT_SCREENSHOT_TIMEOUT_SECONDS,
    ) -> None:
        # Sender is the RTVI server-message dispatch (processor passes
        # ``self._emit_to_widget`` typically). Injected so this module
        # has zero direct Pipecat coupling and the unit tests can stub
        # it with a list-collecting fake.
        self._sender = sender
        self._default_timeout = default_timeout_seconds
        # request_id → Future. Cleaned up on resolve OR on timeout.
        self._pending: dict[str, asyncio.Future[CapturedScreenshot]] = {}

    async def request(
        self,
        *,
        timeout: Optional[float] = None,
    ) -> Optional[CapturedScreenshot]:
        """Ask the widget for a screenshot, await the response, return
        the bytes (or None on timeout / widget error)."""
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[CapturedScreenshot] = loop.create_future()
        self._pending[request_id] = future
        budget = timeout if timeout is not None else self._default_timeout
        started = time.monotonic()
        try:
            await self._sender(
                {"type": "request_screenshot", "request_id": request_id}
            )
        except Exception as e:  # pragma: no cover — sender is robust
            logger.warning(
                f"[screenshot] sender raised — proceeding text-only: {e}"
            )
            self._pending.pop(request_id, None)
            return None

        try:
            result = await asyncio.wait_for(future, timeout=budget)
            return result
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - started) * 1000
            logger.info(
                f"[screenshot] request {request_id} timed out after "
                f"{elapsed:.0f}ms (budget={budget*1000:.0f}ms) — proceeding text-only"
            )
            return None
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, payload: dict) -> None:
        """Called from the processor's on_client_message handler when a
        ``screenshot_response`` arrives. Sets the matching future's
        result (or exception) so the awaiting :meth:`request` returns.

        Tolerant of late / unmatched / malformed responses — those are
        dropped silently, the awaiter will time out on its own."""
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            logger.debug("[screenshot] response without request_id — ignored")
            return
        future = self._pending.get(request_id)
        if future is None:
            # Late response: the awaiter has already timed out and
            # cleaned up. Nothing to do.
            logger.debug(
                f"[screenshot] response for unknown request_id={request_id} "
                "(awaiter likely timed out)"
            )
            return
        if future.done():
            return

        error = payload.get("error")
        if error:
            logger.info(
                f"[screenshot] widget reported capture error: {error}"
            )
            future.set_result(None)  # type: ignore[arg-type]
            return

        image_b64 = payload.get("image_b64")
        if not isinstance(image_b64, str) or not image_b64:
            logger.warning("[screenshot] response missing image_b64 — dropping")
            future.set_result(None)  # type: ignore[arg-type]
            return

        # Cheap shape check — full decode happens at LLM-message
        # construction time. We do NOT decode here because the bytes
        # only need to be base64-clean for the OpenAI image_url path.
        try:
            base64.b64decode(image_b64, validate=True)
        except Exception:
            logger.warning(
                "[screenshot] response image_b64 is not valid base64 — dropping"
            )
            future.set_result(None)  # type: ignore[arg-type]
            return

        captured = CapturedScreenshot(
            image_b64=image_b64,
            mime=str(payload.get("mime") or "image/jpeg"),
            width=_optional_int(payload.get("width")),
            height=_optional_int(payload.get("height")),
            request_id=request_id,
            elapsed_ms=0.0,  # populated at the request site if needed
        )
        future.set_result(captured)

    def cancel_all(self) -> None:
        """Drop all pending futures with a None result. Called on
        processor shutdown so awaiters don't hang past task cancel."""
        for request_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result(None)  # type: ignore[arg-type]
            self._pending.pop(request_id, None)

    @property
    def pending_count(self) -> int:
        """Number of in-flight requests. Test hook."""
        return len(self._pending)


def _optional_int(value: object) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
