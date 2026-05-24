"""Test doubles for the in-app processor's collaborators.

The processor talks to a small set of collaborators we mock here so
state-machine tests run deterministically and offline:

* :class:`FakeRTVIProcessor` — records ``send_server_message`` calls and
  exposes registered ``on_client_message`` handlers so a test can
  simulate ``tool_result`` / ``send-text-message`` / ``heartbeat``
  client messages.
* :class:`FakeAggregator` — minimal stand-in for Pipecat's
  ``LLMUserAggregator`` / ``LLMAssistantAggregator``. Exposes the same
  ``event_handler(name)`` decorator API used by the processor at wire
  time and lets tests fire the registered callbacks.
* :class:`ScriptedStructuredLLM` — replaces what
  ``ChatOpenAI.with_structured_output(schema).astream(messages)``
  yields. Tests script a queue of :class:`InAppAgentOutput`-shaped
  dicts and each ``with_structured_output(...).astream(...)`` call pops
  one. Optionally raises an exception on demand to exercise the
  inference-failure / retry / batch-ceiling paths.
* :class:`FakeChatOpenAI` — wrapper that returns a
  :class:`ScriptedStructuredLLM` from ``with_structured_output``.
* :class:`FakeScreenshotService` — replaces the real broker. Returns a
  scripted ``CapturedScreenshot`` (or ``None``) per round so tests can
  exercise the multimodal-message path AND the timeout / widget-error
  fallback path without spinning real asyncio.Future plumbing.

Single source of truth: the test scripts the LLM's *final* output dict
per round (not chunks). The processor's streaming code paths still
exercise ``LLMFullResponseStartFrame`` / ``LLMTextFrame`` /
``LLMFullResponseEndFrame`` because :meth:`ScriptedStructuredLLM.astream`
yields a single chunk that carries the final ``speech``, which the
processor diff-emits as one delta — that's enough to validate the
streaming framing without exploding the test surface.
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from typing import Any, Callable, Optional

from brain.screenshot_service import CapturedScreenshot

# ──────────────────────────────────────────────────────────────────────
# Aggregator stub
# ──────────────────────────────────────────────────────────────────────


class FakeAggregator:
    """Minimal Pipecat aggregator stand-in.

    The processor only uses ``aggregator.event_handler(event_name)``
    (returning a decorator that registers a callback). We capture
    registrations into ``self.handlers`` and let tests fire them via
    :meth:`fire`.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, list[Callable]] = {}

    def event_handler(self, event_name: str) -> Callable[[Callable], Callable]:
        def register(callback: Callable) -> Callable:
            self.handlers.setdefault(event_name, []).append(callback)
            return callback

        return register

    async def fire(self, event_name: str, *args: Any, **kwargs: Any) -> None:
        for cb in list(self.handlers.get(event_name, ())):
            await cb(self, *args, **kwargs)


# ──────────────────────────────────────────────────────────────────────
# RTVI stub
# ──────────────────────────────────────────────────────────────────────


class FakeClientMessage:
    """Mimics :class:`pipecat.processors.frameworks.rtvi.RTVIClientMessageFrame`
    enough for the processor's ``on_client_message`` handler. The processor
    reads ``msg.type`` and ``msg.data`` only."""

    def __init__(self, *, type: str, data: dict | None = None) -> None:
        self.type = type
        self.data = data or {}


class FakeRTVIProcessor:
    """Stand-in for :class:`RTVIProcessor`.

    Records every ``send_server_message`` payload (the messages the bot
    pushes to the widget) and lets tests fire the
    ``on_client_message`` handler the processor wires up at construction
    via :meth:`deliver_client_message`.
    """

    def __init__(self) -> None:
        self.server_messages: list[dict] = []
        self._client_message_handlers: list[Callable] = []

    def event_handler(self, event_name: str) -> Callable[[Callable], Callable]:
        def register(callback: Callable) -> Callable:
            if event_name == "on_client_message":
                self._client_message_handlers.append(callback)
            return callback

        return register

    async def send_server_message(self, data: dict) -> None:
        self.server_messages.append(data)

    async def deliver_client_message(self, *, type: str, data: dict | None = None) -> None:
        msg = FakeClientMessage(type=type, data=data)
        for cb in list(self._client_message_handlers):
            await cb(self, msg)

    # Convenience accessors used by tests.

    def server_messages_of_type(self, type_: str) -> list[dict]:
        return [m for m in self.server_messages if m.get("type") == type_]

    def last_of_type(self, type_: str) -> Optional[dict]:
        msgs = self.server_messages_of_type(type_)
        return msgs[-1] if msgs else None


# ──────────────────────────────────────────────────────────────────────
# LLM stub — scripted with_structured_output stream
# ──────────────────────────────────────────────────────────────────────


class _Scripted:
    """One scripted LLM round. Either yields chunks ending in ``output_dict``,
    or raises ``raises`` when ``astream`` is awaited."""

    __slots__ = ("output_dict", "raises", "speech_chunks")

    def __init__(
        self,
        output_dict: Optional[dict] = None,
        raises: Optional[BaseException] = None,
        speech_chunks: Optional[list[str]] = None,
    ) -> None:
        self.output_dict = output_dict
        self.raises = raises
        # Optional: progressive speech chunks for streaming-cadence tests.
        # When None, we yield a single chunk carrying the full output_dict
        # which the processor sees as one streaming delta containing all
        # speech at once. That still exercises the LLMText/Start/End path.
        self.speech_chunks = speech_chunks


class ScriptedStructuredLLM:
    """Object returned by ``FakeChatOpenAI.with_structured_output(schema)``.

    Its ``astream(messages)`` async-generator is what the processor's
    ``_llm_round_streaming`` consumes.

    Tests push :class:`_Scripted` items onto ``queue`` via
    :meth:`script_output` / :meth:`script_error`. Each round pops one.
    """

    def __init__(self, schema: dict, queue: deque) -> None:
        self.schema = schema
        self._queue = queue
        # Capture every messages-list and schema actually used so tests
        # can assert against them.
        self.calls: list[dict] = []

    async def astream(self, messages: list[dict]):
        self.calls.append({"schema": self.schema, "messages": list(messages)})
        if not self._queue:
            raise AssertionError(
                "ScriptedStructuredLLM.astream called but no scripted output "
                "was queued. Use harness.script_llm_outputs([...]) before "
                "the action that triggers an LLM round."
            )
        scripted: _Scripted = self._queue.popleft()
        if scripted.raises is not None:
            raise scripted.raises
        out = scripted.output_dict or {}
        if scripted.speech_chunks is not None:
            # Stream cumulative dicts to mimic LangChain's progressive
            # structured-output snapshot semantics.
            speech_so_far = ""
            for chunk in scripted.speech_chunks:
                speech_so_far += chunk
                snapshot = dict(out)
                snapshot["speech"] = speech_so_far
                yield snapshot
            yield out
        else:
            yield out


class FakeChatOpenAI:
    """Replaces the real :class:`langchain_openai.ChatOpenAI`.

    The processor calls ``self._llm.with_structured_output(schema)``
    each round. Each call gets its own :class:`ScriptedStructuredLLM`
    bound to the same scripted-output queue, so the *order* of scripted
    outputs determines what's returned across rounds.
    """

    def __init__(self) -> None:
        self.outputs: deque[_Scripted] = deque()
        self.structured_views: list[ScriptedStructuredLLM] = []

    def with_structured_output(self, schema: dict) -> ScriptedStructuredLLM:
        view = ScriptedStructuredLLM(schema=schema, queue=self.outputs)
        self.structured_views.append(view)
        return view

    # ----- scripting API --------------------------------------------------

    def script_output(
        self,
        output: dict,
        *,
        speech_chunks: Optional[list[str]] = None,
    ) -> None:
        self.outputs.append(_Scripted(output_dict=output, speech_chunks=speech_chunks))

    def script_error(self, exc: Optional[BaseException] = None) -> None:
        self.outputs.append(_Scripted(raises=exc or RuntimeError("scripted LLM failure")))


# ──────────────────────────────────────────────────────────────────────
# Conversation-history summary chain stub
# ──────────────────────────────────────────────────────────────────────


class FakeSummaryChain:
    """Replacement for the LangChain Gemini chain used by
    :class:`InAppConversationHistory`. Returns whatever string the
    test scripted, or raises on demand."""

    class _Result:
        def __init__(self, content: str) -> None:
            self.content = content

    def __init__(self) -> None:
        self.next_summary: str | None = None
        self.raises: BaseException | None = None
        self.calls: list[dict] = []
        self.invoke_count = 0
        self._call_event = asyncio.Event()

    async def ainvoke(self, payload: dict) -> "FakeSummaryChain._Result":
        self.invoke_count += 1
        self.calls.append(payload)
        self._call_event.set()
        if self.raises is not None:
            raise self.raises
        return FakeSummaryChain._Result(self.next_summary or "")

    async def wait_for_invoke(self, timeout: float = 1.0) -> None:
        await asyncio.wait_for(self._call_event.wait(), timeout=timeout)
        self._call_event.clear()


# ──────────────────────────────────────────────────────────────────────
# Screenshot-broker stub
# ──────────────────────────────────────────────────────────────────────


# A tiny base64-encoded byte string that is *valid base64* but is
# A real minimal 1×1 JPEG so the multimodal-message path works
# end-to-end against real OpenAI in Layer 3. The previous placeholder
# (b"\xff\xd8\xff\xd9") was just SOI+EOI with no image data; Layer 2
# never actually decoded it (LangChain just base64-passes), but real
# OpenAI inspects bytes server-side and rejects with image_parse_error.
# This minimal-but-valid JPEG passes that check while being so small
# the LLM gets effectively no visual signal — perfect for scenarios
# where the screenshot is a no-op contextually (off-topic, kickoff,
# product-knowledge questions). Tests that need a specific image
# script it via FakeScreenshotService.script_capture().
_PLACEHOLDER_IMAGE_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8L"
    "CwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUF"
    "BQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4e"
    "Hh4eHh4eHh4eHh4eHh7/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEA"
    "AAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIh"
    "MUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6"
    "Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZ"
    "mqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx"
    "8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREA"
    "AgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAV"
    "YnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hp"
    "anN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPE"
    "xcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD7"
    "LooooA//2Q=="
)


def make_placeholder_screenshot(
    *,
    image_b64: str = _PLACEHOLDER_IMAGE_B64,
    mime: str = "image/jpeg",
    width: int = 800,
    height: int = 600,
    request_id: str = "fake-request",
) -> CapturedScreenshot:
    return CapturedScreenshot(
        image_b64=image_b64,
        mime=mime,
        width=width,
        height=height,
        request_id=request_id,
        elapsed_ms=0.0,
    )


class FakeScreenshotService:
    """In-memory replacement for :class:`brain.screenshot_service.ScreenshotService`.

    The processor calls ``await self._screenshot_service.request()``
    once per inference round. The default behaviour returns a
    placeholder ``CapturedScreenshot`` so the multimodal-message path
    is the path-under-test by default — every existing scenario keeps
    passing, AND the new "image rides on the human marker" contract
    is exercised by every existing Layer 2 test as a side-effect.

    Tests that want to exercise the timeout / widget-error fallback
    path call :meth:`script_timeout` (one shot) or
    :meth:`set_default_to_none`.
    """

    def __init__(self) -> None:
        self.requests: int = 0
        self._scripted: deque[Optional[CapturedScreenshot]] = deque()
        self._default: Optional[CapturedScreenshot] = make_placeholder_screenshot()
        # Track the number of currently in-flight ``request()`` calls,
        # mirroring the real ``ScreenshotService.pending_count``. The
        # processor reads this to decide whether to arm the idle
        # timer — if a screenshot fetch is in flight, the agent is
        # actively working on the visitor's behalf, so the "still
        # there?" check-in must NOT fire. Increment at request
        # entry, decrement on return so the counter is correct from
        # the awaiter's perspective.
        self._in_flight: int = 0

    async def request(
        self, *, timeout: Optional[float] = None
    ) -> Optional[CapturedScreenshot]:
        self.requests += 1
        self._in_flight += 1
        try:
            if self._scripted:
                return self._scripted.popleft()
            return self._default
        finally:
            self._in_flight -= 1

    def resolve(self, payload: dict) -> None:
        # Real service routes inbound widget responses; the fake
        # ignores them — tests script captured values directly.
        return

    def cancel_all(self) -> None:
        self._scripted.clear()

    @property
    def pending_count(self) -> int:
        return self._in_flight

    # ── scripting API ───────────────────────────────────────────────

    def script_capture(
        self,
        captured: Optional[CapturedScreenshot] = None,
    ) -> None:
        """Queue one specific result for the next ``request()`` call.

        Pass ``None`` to script a "widget didn't respond / errored"
        round; pass a built :class:`CapturedScreenshot` for a custom
        image (e.g. with a known token in the bytes for verification)."""
        self._scripted.append(captured)

    def script_timeout(self) -> None:
        """Queue one ``None`` result, equivalent to a budget-blown
        timeout from the real broker."""
        self._scripted.append(None)

    def set_default_to_none(self) -> None:
        """All future requests return ``None`` until reset. Useful for
        whole-test fallback-behaviour assertions."""
        self._default = None

    def set_default_capture(
        self, captured: Optional[CapturedScreenshot]
    ) -> None:
        self._default = captured
