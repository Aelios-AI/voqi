"""Test harness for :class:`InAppAgentProcessor`.

Goal: exercise every state-machine branch in
``brain/processor.py`` deterministically, offline, and
without spinning up the full Pipecat pipeline.

How it works
------------

1. Construct a real :class:`InAppAgentProcessor` with fake
   collaborators (RTVI, aggregators) so wire-time hookups succeed.
2. Replace ``processor._llm`` with a :class:`FakeChatOpenAI` so each
   inference round consumes a scripted dict instead of calling OpenAI.
3. Patch the conversation-history summary chain so summarization tests
   are deterministic and offline.
4. Override ``create_task`` / ``cancel_task`` / ``push_frame`` on the
   processor so we don't need Pipecat's TaskManager / pipeline. Tasks
   become plain ``asyncio.create_task`` calls; pushed frames are
   recorded into ``self.pushed_frames`` for assertions.
5. Drive the processor via its public-ish entry points
   (``_handle_user_input``, ``_on_tool_outcome``, the queued
   priority-queue pump). Tests assert against
   ``rtvi.server_messages``, ``pushed_frames``, processor-internal
   state, and the active-demo / batch state.

Time control: tests that need timeouts to fire (incomplete-prompt 5s,
batch 60s, response-timeout 15s) shrink the timeout value via
:meth:`set_timeouts` and use :meth:`advance` to run the loop until the
expected work happens. This keeps each scenario millisecond-fast
without monkeypatching ``asyncio.sleep`` globally.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Optional

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)

from brain.canned_speech import CannedKey
from brain.config import InAppRuntimeConfig, InAppTool
from brain.frames import (
    InAppMessageFrame,
    MessageType,
)
from brain.processor import InAppAgentProcessor

from .fakes import (
    FakeAggregator,
    FakeChatOpenAI,
    FakeRTVIProcessor,
    FakeScreenshotService,
    FakeSummaryChain,
)


def make_runtime_config(
    *,
    tools: Optional[list[InAppTool]] = None,
    software_name: str = "TestSoftware",
    software_docs: Optional[str] = None,
    software_tldr: Optional[str] = "A test app.",
    additional_instructions: Optional[str] = None,
    ai_profile: Optional[dict] = None,
    language: str = "en",
    mode: str = "action",
) -> InAppRuntimeConfig:
    return InAppRuntimeConfig(
        session_uuid="00000000-0000-0000-0000-000000000000",
        software_uuid="soft-uuid",
        software_name=software_name,
        additional_instructions=additional_instructions,
        software_docs=software_docs,
        software_tldr=software_tldr,
        tools=tools or [],
        ai_profile=ai_profile,
        language=language,
        mode=mode,
    )


def tool(
    name: str,
    *,
    description: str = "test tool",
    requires_confirmation: bool = False,
    parameters: Optional[dict] = None,
) -> InAppTool:
    return InAppTool(
        name=name,
        description=description,
        parameters=parameters or {"type": "object", "properties": {}},
        requires_confirmation=requires_confirmation,
    )


class ProcessorHarness:
    """One harness per test. Drives the real processor with fakes."""

    def __init__(
        self,
        *,
        runtime_config: Optional[InAppRuntimeConfig] = None,
        output_language: str = "English",
        language_code: str = "en",
    ) -> None:
        self.rtvi = FakeRTVIProcessor()
        self.user_aggregator = FakeAggregator()
        self.assistant_aggregator = FakeAggregator()
        self.config = runtime_config or make_runtime_config()

        self.processor = InAppAgentProcessor(
            rtvi=self.rtvi,  # type: ignore[arg-type]
            user_aggregator=self.user_aggregator,  # type: ignore[arg-type]
            assistant_aggregator=self.assistant_aggregator,  # type: ignore[arg-type]
            runtime_config=self.config,
            output_language=output_language,
            language_code=language_code,
            kickoff_received_event=None,
        )

        # Replace the LLM with our scripted fake.
        self.llm = FakeChatOpenAI()
        self.processor._llm = self.llm  # type: ignore[attr-defined]

        # Replace the screenshot broker with a fake that returns a
        # placeholder ``CapturedScreenshot`` by default. Every existing
        # Layer 2 test thus exercises the multimodal-message path
        # automatically; tests that want to assert the
        # text-only-fallback path call ``harness.screenshots.script_timeout()``
        # before the round.
        self.screenshots = FakeScreenshotService()
        self.processor._screenshot_service = self.screenshots  # type: ignore[attr-defined]

        # Replace the summary chain with a fake so history tests don't
        # need google credentials. Conversation history also won't fire
        # on its own at low message counts, so this only matters when a
        # test pushes the window past max_window.
        self.summary_chain = FakeSummaryChain()
        self.processor._history._summary_chain = self.summary_chain  # type: ignore[attr-defined]

        # Recording sinks.
        self.pushed_frames: list[Frame] = []
        self.canned_spoken: list[CannedKey] = []
        # Track demonstration history (action, demo_id, name).
        self.demo_events: list[tuple[str, str, str]] = []
        # Track batch dispatch / clear / timeout events.
        self.batch_events: list[tuple[str, dict]] = []
        # Assistant turns assembled from streamed LLMTextFrame frames
        # (between LLMFullResponseStart and LLMFullResponseEnd). Tests
        # use this to inspect what the agent actually said. Mirrors
        # what the widget's transcript builder constructs from the
        # same frames in production.
        self.assistant_speech_history: list[dict] = []
        self._current_assistant_buffer: list[str] = []
        self._inside_assistant_turn = False
        # Per-tool outcomes recorded by hooking _on_tool_outcome —
        # mirrors what the widget's `registry.log()` builds in
        # production for the session-complete POST.
        self.tool_outcome_log: list[dict] = []
        # True while a round is actively running. pump_until_idle
        # waits for it to clear before returning.
        self._round_in_flight = False

        # Patch processor lifecycle methods so we don't need
        # Pipecat's TaskManager / pipeline machinery.
        self._tasks: set[asyncio.Task] = set()
        self._monkeypatch_processor()

        # Hook canned speech to record + still play through the real path.
        original_speak = self.processor._speak_canned

        def recording_speak(
            key: CannedKey,
            *,
            put_back_when_interrupted: bool = False,
        ) -> None:
            # ``_speak_canned`` is synchronous now: it enqueues a
            # CANNED_SPEECH frame on the priority queue. The actual
            # frame push happens later when the pump processes the
            # frame (see ``_handle_canned_speech``). Tests that wait
            # on ``pump_until_idle`` will see both the canned_spoken
            # record (set here at enqueue time) AND the pushed frames
            # (set after the pump processes the frame). Forwards
            # ``put_back_when_interrupted`` so apology paths
            # (force-end-demo with INFERENCE_RETRY_EXHAUSTED /
            # BATCH_CEILING_HIT) behave identically here and in
            # production.
            self.canned_spoken.append(key)
            original_speak(key, put_back_when_interrupted=put_back_when_interrupted)

        self.processor._speak_canned = recording_speak  # type: ignore[assignment]

        # Hook _start_new_demonstration / _end_current_demonstration to record.
        orig_start = self.processor._start_new_demonstration
        orig_end = self.processor._end_current_demonstration

        async def recording_start(name: str) -> str:
            new_id = await orig_start(name)
            self.demo_events.append(("start_new", new_id, name))
            return new_id

        async def recording_end() -> None:
            ended = (
                self.processor._active_demonstration.id
                if self.processor._active_demonstration is not None
                else ""
            )
            ended_name = (
                self.processor._active_demonstration.name
                if self.processor._active_demonstration is not None
                else ""
            )
            await orig_end()
            self.demo_events.append(("end_current", ended, ended_name))

        self.processor._start_new_demonstration = recording_start  # type: ignore[assignment]
        self.processor._end_current_demonstration = recording_end  # type: ignore[assignment]

        # Hook _dispatch_batch_now to record.
        orig_dispatch = self.processor._dispatch_batch_now

        async def recording_dispatch(*, internal_tool_calls, demo_id, batch_id):
            self.batch_events.append(
                (
                    "dispatch",
                    {
                        "demo_id": demo_id,
                        "batch_id": batch_id,
                        "size": len(internal_tool_calls),
                        "tool_calls": [
                            {
                                "name": itc["name"],
                                "id": itc["id"],
                                "arguments": itc["arguments"],
                            }
                            for itc in internal_tool_calls
                        ],
                    },
                )
            )
            await orig_dispatch(
                internal_tool_calls=internal_tool_calls,
                demo_id=demo_id,
                batch_id=batch_id,
            )

        self.processor._dispatch_batch_now = recording_dispatch  # type: ignore[assignment]

        # Record per-tool outcomes via _on_tool_outcome wrap. We only
        # record outcomes that actually LANDED — the production
        # _on_tool_outcome has four staleness guards (demo_id mismatch,
        # batch_id mismatch, call_id missing, no in-flight batch); we
        # detect a successful land by observing whether the active
        # demonstration's batch-history invocation entry flipped from
        # `in_progress` to a terminal status.
        from datetime import datetime, timezone

        from brain.tool_dispatcher import ToolDispatchOutcome

        orig_on_outcome = self.processor._on_tool_outcome

        async def recording_on_outcome(
            demo_id, batch_id, call_id, name, args, outcome, payload
        ):
            await orig_on_outcome(
                demo_id, batch_id, call_id, name, args, outcome, payload
            )
            # Did the orig call actually update the batch history? If
            # not, this outcome was stale and should NOT be logged.
            active = self.processor._active_demonstration
            landed = False
            if active is not None and active.id == demo_id:
                for batch in active.batches_history:
                    if batch.batch_id != batch_id:
                        continue
                    for inv in batch.invocations:
                        if inv.call_id == call_id and inv.status != "in_progress":
                            landed = True
                            break
                    break
            if not landed:
                return
            if outcome == ToolDispatchOutcome.SUCCESS:
                result = {"result": payload}
            elif outcome == ToolDispatchOutcome.FAILED:
                result = {"error": str(payload)}
            else:
                return
            self.tool_outcome_log.append(
                {
                    "toolName": name,
                    "args": args,
                    "result": result,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        self.processor._on_tool_outcome = recording_on_outcome  # type: ignore[assignment]
        self.processor._tool_dispatcher._on_outcome = recording_on_outcome
        # Note: ToolDispatcher captured the original callback at
        # construction; rebind it on the instance so recordings flow.

        # Wrap _llm_round_streaming so pump_until_idle can wait on the
        # round to actually finish (matters for real-LLM Layer 3 tests).
        orig_round_streaming = self.processor._llm_round_streaming

        async def tracked_round_streaming(*, wake_mode, batch_state, wake_frame=None):
            self._round_in_flight = True
            try:
                return await orig_round_streaming(
                    wake_mode=wake_mode,
                    batch_state=batch_state,
                    wake_frame=wake_frame,
                )
            finally:
                self._round_in_flight = False

        self.processor._llm_round_streaming = tracked_round_streaming  # type: ignore[assignment]

        # Mirror the real session's startup: _start() creates the pump
        # task at session boot so the message queue is always being
        # drained. Tests that enqueue frames directly (e.g. the canned-
        # speech-routing tests) rely on this; tests that call
        # send_user / send_kickoff / send_text_message recreate the pump
        # via their legitimate paths anyway, so this initial create is
        # idempotent for them.
        self.processor._create_pump_task()

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def _monkeypatch_processor(self) -> None:
        """Replace FrameProcessor lifecycle hooks the processor needs."""
        loop = asyncio.get_event_loop()

        def fake_create_task(coro, name: Optional[str] = None) -> asyncio.Task:
            task = loop.create_task(coro, name=name or "test-task")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return task

        async def fake_cancel_task(task: asyncio.Task, timeout: float = 1.0) -> None:
            if task.done():
                return
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except (asyncio.CancelledError, asyncio.TimeoutError, BaseException):
                pass

        async def fake_push_frame(frame: Frame, direction=None) -> None:
            self.pushed_frames.append(frame)
            # Mirror Pipecat's universal-aggregator behaviour: when a real
            # LLMFullResponseStart/End is pushed downstream, the assistant
            # aggregator fires the matching turn lifecycle event. The
            # processor relies on those handlers to (a) notify the
            # response-timeout watchdog, and (b) resume the message-pump
            # task that paused inside _process_message.
            if isinstance(frame, LLMFullResponseStartFrame):
                self._inside_assistant_turn = True
                self._current_assistant_buffer = []
                await self.assistant_aggregator.fire("on_assistant_turn_started")
            elif isinstance(frame, LLMFullResponseEndFrame):
                if self._inside_assistant_turn:
                    content = "".join(self._current_assistant_buffer)
                    if content:
                        self.assistant_speech_history.append(
                            {"role": "assistant", "content": content}
                        )
                    self._inside_assistant_turn = False
                    self._current_assistant_buffer = []
                await self.assistant_aggregator.fire("on_assistant_turn_stopped")
            elif isinstance(frame, LLMTextFrame) and self._inside_assistant_turn:
                self._current_assistant_buffer.append(getattr(frame, "text", ""))

        self.processor.create_task = fake_create_task  # type: ignore[assignment]
        self.processor.cancel_task = fake_cancel_task  # type: ignore[assignment]
        self.processor.push_frame = fake_push_frame  # type: ignore[assignment]

    async def shutdown(self) -> None:
        """Cancel anything still running. Tests should call this in a fixture."""
        # Cancel processor's message-pump and timer tasks.
        try:
            await self.processor._cancel_pump_task()
        except Exception:
            pass
        try:
            await self.processor._cancel_reply_watchdog()
        except Exception:
            pass
        try:
            await self.processor._cancel_incomplete_timeout()
        except Exception:
            pass
        # Cancel any remaining harness tasks.
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.processor._tool_dispatcher.shutdown()
        # Drop any in-flight screenshot awaiters so they don't hang
        # past pipeline tear-down.
        try:
            self.processor._screenshot_service.cancel_all()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # LLM scripting passthrough
    # ------------------------------------------------------------------

    def script_llm_outputs(
        self,
        outputs: Iterable[dict],
        *,
        speech_chunks_per_round: Optional[list[Optional[list[str]]]] = None,
    ) -> None:
        outputs = list(outputs)
        chunks = speech_chunks_per_round or [None] * len(outputs)
        for out, ch in zip(outputs, chunks):
            self.llm.script_output(out, speech_chunks=ch)

    def script_llm_error(self, n: int = 1, exc: Optional[BaseException] = None) -> None:
        for _ in range(n):
            self.llm.script_error(exc)

    # ------------------------------------------------------------------
    # Driving inputs
    # ------------------------------------------------------------------

    async def send_user(self, text: str) -> None:
        """Simulate a final voice transcript landing on the processor."""
        await self.processor._handle_user_input(text)
        await self.pump_until_idle()

    async def send_text_message(self, text: str) -> None:
        """Simulate a 'send-text-message' RTVI client message."""
        await self.rtvi.deliver_client_message(
            type="send-text-message", data={"text": text}
        )
        await self.pump_until_idle()

    async def send_kickoff(self) -> None:
        """Enqueue a kickoff frame manually (the processor's normal entry
        point parses a Pipecat SessionOpenFrame; bypassing that frame plumbing
        keeps the test offline). Behaviour-wise mirrors the real
        :meth:`process_frame` SessionOpenFrame branch: flips
        ``_session_started`` (the idle-timer arm helper gates on this),
        seeds ``_last_heartbeat_time``, then enqueues the priority-1
        KICKOFF frame."""
        import time as _time

        self.processor._session_started = True
        self.processor._last_heartbeat_time = _time.time()
        self.processor._enqueue(
            priority=1,
            frame=InAppMessageFrame(
                message="",
                message_type=MessageType.KICKOFF,
                put_back_when_interrupted=False,
            ),
        )
        self.processor._create_pump_task()
        await self.pump_until_idle()

    async def deliver_tool_result(
        self,
        *,
        call_id: str,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        """Simulate the widget posting a 'tool_result' back."""
        data: dict = {"call_id": call_id}
        if error is not None:
            data["error"] = error
        else:
            data["result"] = result
        await self.rtvi.deliver_client_message(type="tool_result", data=data)
        # Outcome callback is awaited from the dispatcher's per-call task.
        await self._yield()
        await self.pump_until_idle()

    async def fire_user_aggregator(self, event: str) -> None:
        await self.user_aggregator.fire(event)

    async def fire_assistant_aggregator(self, event: str) -> None:
        await self.assistant_aggregator.fire(event)

    # ------------------------------------------------------------------
    # Message-pump driving
    # ------------------------------------------------------------------

    async def pump_until_idle(self, *, total_timeout: float = 60.0) -> None:
        """Drain the priority queue + active rounds.

        The processor pauses on each round (``_pause_processing``) and
        only resumes when the assistant aggregator fires
        ``on_assistant_turn_stopped`` (in real Pipecat that comes off
        ``LLMFullResponseEndFrame``; the harness's ``push_frame`` mirror
        also fires it on its own).

        Polling cadence depends on the LLM in use. With the scripted
        :class:`FakeChatOpenAI` rounds resolve synchronously through
        zero-time awaits, so we yield with ``asyncio.sleep(0)`` and
        finish in microseconds — that matters for tests with sub-second
        timeouts (e.g. the incomplete-prompt suite at 50ms). With the
        real :class:`ChatOpenAI` rounds take seconds, so we poll on
        20ms ticks.

        Settled = queue empty AND not blocked AND no round currently
        in flight (we track that via ``_round_in_flight``).
        """
        # Detect the fake LLM by attribute (avoids a cross-module import).
        is_fake_llm = hasattr(self.processor._llm, "outputs")
        tick = 0.0 if is_fake_llm else 0.02
        idle_threshold = 5 if is_fake_llm else 3
        loop = asyncio.get_event_loop()
        deadline = loop.time() + total_timeout
        idle_streak = 0
        while loop.time() < deadline:
            if (
                self.processor._processing_blocked
                and self.processor._pump_task is not None
            ):
                await self.assistant_aggregator.fire("on_assistant_turn_stopped")
                idle_streak = 0
                await asyncio.sleep(tick)
                continue
            if not self.processor._wake_queue.empty():
                idle_streak = 0
                await asyncio.sleep(tick)
                continue
            if self._round_in_flight:
                idle_streak = 0
                await asyncio.sleep(tick)
                continue
            # Guide-mode short-circuit spawns a background screenshot
            # fetch that itself enqueues a SCREENSHOT_RESULT frame —
            # if the pump declares idle BEFORE that task lands its
            # frame, we miss the second round entirely. Treat any
            # in-flight screenshot-fetch task as "not idle yet" so
            # the screenshot_result round downstream gets to run.
            # Long-running pump / idle / heartbeat tasks are filtered
            # out by name (they're always "not done" by design and
            # would otherwise prevent pump_until_idle from ever
            # returning).
            screenshot_in_flight = any(
                not t.done()
                and (t.get_name() or "").startswith(
                    ("in-app-screenshot-fetch", "in-app-guide-screenshot-fetch")
                )
                for t in self._tasks
            )
            if screenshot_in_flight:
                idle_streak = 0
                await asyncio.sleep(tick)
                continue
            await asyncio.sleep(tick)
            idle_streak += 1
            if idle_streak >= idle_threshold:
                return

    @staticmethod
    async def _yield(n: int = 3) -> None:
        for _ in range(n):
            await asyncio.sleep(0)

    # ------------------------------------------------------------------
    # Time-shrink helpers (for testing timeouts without real waits)
    # ------------------------------------------------------------------

    def set_timeouts(
        self,
        *,
        batch_seconds: Optional[float] = None,
        response_seconds: Optional[float] = None,
        incomplete_short: Optional[float] = None,
        incomplete_long: Optional[float] = None,
        heartbeat_seconds: Optional[float] = None,
        idle_warning_after: Optional[float] = None,
        idle_end_after_warning: Optional[float] = None,
    ) -> None:
        """Shrink per-instance timeouts so timeout tests run in
        milliseconds instead of seconds. All mutations are scoped to
        ``self.processor`` so concurrent tests can't pollute each
        other's timing knobs."""
        if batch_seconds is not None:
            self.processor._batch_timeout_seconds = batch_seconds
        if response_seconds is not None:
            self.processor._reply_watchdog_seconds = response_seconds
        if incomplete_short is not None or incomplete_long is not None:
            # Incomplete-prompt follow-up timers were removed (this
            # widget is a passive listener; we don't nudge the
            # visitor to continue if they trail off). Tests passing
            # these knobs are working from the old contract.
            raise RuntimeError(
                "incomplete_short / incomplete_long timeout knobs are "
                "obsolete — the follow-up timer was removed. The LLM "
                "still classifies user_turn_status; nothing fires "
                "after a configured delay."
            )
        if heartbeat_seconds is not None:
            self.processor._heartbeat_timeout_seconds = heartbeat_seconds
        if idle_warning_after is not None:
            self.processor._idle_warning_after_seconds = idle_warning_after
        if idle_end_after_warning is not None:
            self.processor._idle_end_after_warning_seconds = idle_end_after_warning

    def set_inference_retry_limit(self, n: int) -> None:
        """Per-instance retry cap. Was a module global; now per
        processor so concurrent scenarios stay isolated."""
        self.processor._inference_retry_limit = n

    def set_history_window(
        self, *, max_window: int, summarize_chunk: int
    ) -> None:
        """Per-instance conversation-history window. Useful for
        triggering summarization mid-test without driving 30+
        messages."""
        assert summarize_chunk < max_window, (
            "summarize_chunk must be smaller than max_window"
        )
        self.processor._history._max_window = max_window
        self.processor._history._summarize_chunk = summarize_chunk

    def set_max_batches_per_demo(self, n: int) -> None:
        """Per-instance batch ceiling. Mutates the processor's own
        ``_max_tool_batches_per_demonstration`` attribute so concurrent
        Layer 3 scenarios using different ceilings don't corrupt each
        other's prompt rendering."""
        self.processor._max_tool_batches_per_demonstration = n

    # ------------------------------------------------------------------
    # State assertions
    # ------------------------------------------------------------------

    def assert_active_demo(
        self,
        *,
        name: Optional[str] = None,
        batches_dispatched: Optional[int] = None,
    ) -> None:
        active = self.processor._active_demonstration
        assert active is not None, "expected active demonstration; got None"
        if name is not None:
            assert active.name == name, (
                f"active demo name mismatch: expected {name!r}, got {active.name!r}"
            )
        if batches_dispatched is not None:
            assert active.tool_batches_dispatched == batches_dispatched, (
                f"batches_dispatched mismatch: expected {batches_dispatched}, "
                f"got {active.tool_batches_dispatched}"
            )

    def assert_no_active_demo(self) -> None:
        assert self.processor._active_demonstration is None, (
            f"expected no active demo; got {self.processor._active_demonstration}"
        )

    def assert_in_flight(self, *, expected_size: Optional[int] = None) -> None:
        ifb = self.processor._in_flight_batch
        assert ifb is not None, "expected in-flight batch; got None"
        if expected_size is not None:
            assert ifb.expected_size == expected_size

    def assert_no_in_flight(self) -> None:
        assert self.processor._in_flight_batch is None, (
            f"expected no in-flight batch; got {self.processor._in_flight_batch}"
        )

    def assert_pending(self, *, confirmable: Optional[list[str]] = None) -> None:
        pending = self.processor._pending_confirmation_batch
        assert pending is not None, "expected pending-confirmation batch; got None"
        if confirmable is not None:
            assert sorted(pending.confirmable_tool_names) == sorted(confirmable)

    def assert_no_pending(self) -> None:
        assert self.processor._pending_confirmation_batch is None

    def assert_batch_state(self, expected: str) -> None:
        actual = self.processor._compute_batch_state()
        assert actual == expected, f"batch state mismatch: expected {expected}, got {actual}"

    # ------------------------------------------------------------------
    # Convenience for tests that need to reach the dispatcher's recorded
    # tool_call_batch (which is what the widget actually sees).
    # ------------------------------------------------------------------

    def last_dispatched_batch(self) -> Optional[dict]:
        msgs = self.rtvi.server_messages_of_type("tool_call_batch")
        return msgs[-1] if msgs else None

    def last_internal_dispatch(self) -> Optional[dict]:
        for kind, payload in reversed(self.batch_events):
            if kind == "dispatch":
                return payload
        return None

    def last_call_id(self, *, name: Optional[str] = None) -> Optional[str]:
        """Most recent call_id we dispatched (optionally filtered by tool name)."""
        last = self.last_internal_dispatch()
        if last is None:
            return None
        for tc in reversed(last["tool_calls"]):
            if name is None or tc["name"] == name:
                return tc["id"]
        return None
