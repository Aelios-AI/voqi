"""The agent processor — Pipecat FrameProcessor that drives the LLM loop.

Owns the priority queue, the per-turn structured-output call, tool
dispatch, the demonstration state machine, and the background
watchdogs (idle, response-timeout, session-cap, kickoff). Sits between
the user-side aggregator and the TTS in the Pipecat pipeline.

Key behaviours:

  1. **Demonstrations are cancellable.** A "demonstration" tags every
     tool batch the agent fires in service of a single user request.
     If the visitor redirects mid-demo, in-flight batches are dropped
     and stale results are filtered out — see :mod:`tool_dispatcher`.

  2. **Parallel tool calls.** The LLM can emit a list of tool calls in
     a single response; the dispatcher fires them all in parallel and
     only wakes the agent when the *batch* resolves. The agent's reply
     to that batch is a streamed visible response (a progress
     announcement) before the next round.

  3. **History is summarized in the background.** Sessions can run
     long; :class:`InAppConversationHistory` runs a background
     summarizer when the log exceeds a window, replacing the oldest
     chunk with a single rolling summary so the LLM's context window
     never overflows.

  4. **The processor pushes text frames, not audio.** Spoken replies
     leave this processor as ``LLMTextFrame``s, bracketed by
     ``LLMFullResponseStart/End``. Cartesia TTS downstream synthesises
     them; the same start/end pair fires
     ``on_assistant_turn_started`` / ``on_assistant_turn_stopped`` on
     the universal assistant aggregator, which the processor surfaces
     as RTVI server messages (``assistant_turn_started`` /
     ``assistant_turn_ended``) so the widget can update its
     "Responding…" state.

Priority queue ordering (see :class:`brain.frames.RankedEnvelope`):

    0  SYSTEM recovery        — urgent injection from internal handlers
    1  USER_MESSAGE / TEXT_MESSAGE / KICKOFF / SCREENSHOT_RESULT
                              — preempt pending tool batches
    3  TOOL_BATCH_COMPLETED
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from langchain_openai import ChatOpenAI
from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    CancelTaskFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    StartFrame,
)
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregator,
    LLMUserAggregator,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIClientMessageFrame, RTVIProcessor

from .agent_output import (
    InAppAgentOutput,
    InAppToolInvocation,
    build_in_app_schema,
)
from .canned_speech import CannedKey, render_canned
from .config import (
    IN_APP_AGENT_GUIDE_TURN_TEMPLATE,
    IN_APP_AGENT_HUMAN_PROMPT,
    IN_APP_AGENT_TURN_TEMPLATE,
    InAppRuntimeConfig,
)
from .conversation_history import InAppConversationHistory
from .frames import InAppMessageFrame, MessageType, RankedEnvelope
from .screenshot_service import (
    CapturedScreenshot,
    ScreenshotService,
)
from .tool_dispatcher import (
    ToolDispatcher,
    ToolDispatchOutcome,
)

# Heartbeat: widget sends ``{"type": "heartbeat"}`` periodically. If we
# go this long without one, the bot terminates itself upstream.
HEARTBEAT_TIMEOUT_SECONDS = 60.0
HEARTBEAT_CHECK_INTERVAL_SECONDS = 5.0

# If the message processor accepts work but doesn't push a response
# within this window, fire the recovery path (interrupt + soft error).
REPLY_WATCHDOG_SECONDS = 15.0

# Built-in default OpenAI model id; deployments override via
# ``llm_model:`` in ``voqi.config.yaml``.
OPENAI_MODEL = "gpt-5.4"

# Hard upper bound on how long a single tool batch may take to fully
# resolve (counted from dispatch). At the cap we force-complete any
# still-pending tools with an error message and feed the partial result
# set to the next inference. Same shape as a normal TOOL_BATCH_COMPLETED.
# Built-in default; deployments override via ``batch_timeout_seconds:``
# in ``voqi.config.yaml``.
BATCH_TIMEOUT_SECONDS = 60.0

# How many times we'll retry a TOOL_BATCH_COMPLETED inference before
# giving up. On the (cap+1)-th failure we apologise hard and force
# end_current on the active demonstration, freeing the visitor to ask
# for something else.
INFERENCE_RETRY_LIMIT = int(
    os.getenv("IN_APP_TOOL_BATCH_INFERENCE_RETRY_LIMIT", "3")
)

# Per-demonstration ceiling on dispatched tool batches. Catches runaway
# loops where the LLM keeps queueing follow-up batches forever. When the
# next batch would exceed this we apologise and force end_current.
# Built-in default; deployments override via
# ``max_tool_batches_per_demonstration:`` in ``voqi.config.yaml``.
MAX_TOOL_BATCHES_PER_DEMONSTRATION = 8

# Idle session timeout (visitor went quiet on the page). The widget is
# embedded passively, so the visitor may have wandered off. Stages:
#   1. After IDLE_WARNING_AFTER_SECONDS of NO user input, fire a SYSTEM
#      wake telling the agent to ask whether the visitor still needs
#      help and to warn that the session will close in
#      IDLE_END_AFTER_WARNING_SECONDS if they don't reply.
#   2. If the visitor replies (any user input) the timer resets.
#   3. If they stay quiet through stage 1's grace period we force-end
#      the session with a brief goodbye.
# Measured from the last user input (USER_MESSAGE / TEXT_MESSAGE wake)
# OR from session start if no input has happened yet.
IDLE_WARNING_AFTER_SECONDS = float(
    os.getenv("IN_APP_IDLE_WARNING_AFTER_SECONDS", "120.0")
)
IDLE_END_AFTER_WARNING_SECONDS = float(
    os.getenv("IN_APP_IDLE_END_AFTER_WARNING_SECONDS", "60.0")
)
IDLE_CHECK_INTERVAL_SECONDS = float(
    os.getenv("IN_APP_IDLE_CHECK_INTERVAL_SECONDS", "5.0")
)

# Session-cap heads-up. Sessions hard-close at the 90-minute mark
# (widget-side timer + server-side wait_for at 100 min as a backup);
# at 80 min we speak a canned heads-up so the visitor has a chance
# to wrap up before the disconnect. Independent of the idle timer —
# fires once per session regardless of activity.
SESSION_CAP_WARNING_AFTER_SECONDS = float(
    os.getenv("IN_APP_SESSION_CAP_WARNING_AFTER_SECONDS", "4800.0")
)

# Per-inference screenshot budget. The widget snapshots its host page
# via html2canvas and posts the JPEG back over the RTVI client-message
# channel. We always request a screenshot before EVERY inference (no
# per-wake gating — the agent's view of the screen should be coherent
# with the visitor's, even on tool_batch_completed and kickoff wakes).
# If the round-trip exceeds this budget we proceed text-only — never
# block inference indefinitely on a screenshot.
SCREENSHOT_TIMEOUT_SECONDS = float(
    os.getenv("IN_APP_SCREENSHOT_TIMEOUT_SECONDS", "2.0")
)


# ──────────────────────────────────────────────────────────────────────
# Incomplete-prompt timeouts
# ──────────────────────────────────────────────────────────────────────
# The same structured-output LLM call decides everything: speech, tool
# calls, turn completeness, demonstration action. See :mod:`agent_output`
# — the schema's own field descriptions tell the model how to populate
# each field directly. No extra per-round "do this" instruction is needed.


def _summarise_client_message_data(msg_type: str, data: object) -> str:
    """Render a short, log-safe summary of an inbound RTVI client
    message's data field. The default ``f"{msg.data}"`` would dump
    the entire dict — including the ~50–500 KB base64 image inside
    a ``screenshot_response`` — which floods the trace and hides the
    actual flow. We pull out just the structural bits (request_id,
    image size, etc.) and log those; consumers downstream still get
    the full bytes via the routed handler call.

    Tool results are similarly noisy in the other direction (results
    can be large JSON blobs), so they get a length-only summary too.
    For everything else we fall back to the raw dict — those types
    are small (heartbeat, send-text-message) and useful to see in full.
    """
    if not isinstance(data, dict):
        return repr(data)
    if msg_type == "screenshot_response":
        bytes_len = len(data.get("image_b64") or "")
        keys = ["request_id", "mime", "width", "height", "error"]
        summary = {k: data[k] for k in keys if k in data}
        if bytes_len:
            summary["image_b64_chars"] = bytes_len
        return repr(summary)
    if msg_type == "tool_result":
        result_repr = repr(data.get("result"))
        if len(result_repr) > 200:
            result_repr = result_repr[:200] + "…"
        summary = {
            "call_id": data.get("call_id"),
            "batch_id": data.get("batch_id"),
            "result": result_repr,
            "error": data.get("error"),
        }
        return repr({k: v for k, v in summary.items() if v is not None})
    return repr(data)


def _build_inference_messages(
    *,
    system_prompt: str,
    captured: Optional[CapturedScreenshot],
) -> list:
    """Build the two-message inference shape for ChatOpenAI.

    Always returns ``[(system, prompt), human]`` where ``human`` is
    either:

      * a plain string marker (when ``captured`` is None — the
        screenshot timed out or the widget reported an error), or
      * a multimodal content-block list ``[{type:"text",...},
        {type:"image_url",...}]`` (when bytes are present).

    The image rides on the human marker rather than being injected
    into the rendered system prompt so the prompt rendering stays a
    pure string operation testable without any image plumbing — and so
    the LLM treats the screenshot as part of "the trigger for this
    turn", which is semantically correct (it's a snapshot of the
    screen at the moment of inference).
    """
    if captured is None:
        return [
            ("system", system_prompt),
            ("human", IN_APP_AGENT_HUMAN_PROMPT),
        ]
    image_url = f"data:{captured.mime};base64,{captured.image_b64}"
    return [
        ("system", system_prompt),
        (
            "human",
            [
                {"type": "text", "text": IN_APP_AGENT_HUMAN_PROMPT},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        ),
    ]

@dataclass
class InvocationHistoryEntry:
    """One tool invocation inside a batch's history record.

    Lives on :class:`BatchHistoryEntry` and surfaces into the per-round
    state context. ``status`` reflects what we know right now:

      * ``in_progress`` — set at dispatch time, before the widget
        result lands.
      * ``success``    — widget returned ``{"result": ...}``.
      * ``error``      — widget returned ``{"error": ...}``.
      * ``timeout``    — the per-batch timeout fired before this call
        returned and we force-finalized it with an error.
      * ``cancelled``  — demo was interrupted (start_new / end_current
        cascade) before this call had a chance to land.

    ``call_id`` is the OpenAI-protocol id we minted at dispatch (also
    threaded into the assistant's tool_calls message and the matching
    tool result message in conversation history).
    """

    call_id: str
    name: str
    arguments: dict
    status: Literal["in_progress", "success", "error", "timeout", "cancelled"] = "in_progress"
    result: Any = None


@dataclass
class BatchHistoryEntry:
    """One batch within a demonstration's history.

    Recorded eagerly at dispatch time (every invocation in the batch
    appears with status=``in_progress``) and updated as results land.
    Renders into the state-context block grouped by batch so the LLM
    sees: which batches ran in this demo, in what order, what was in
    each batch (parallel), and what each invocation returned.

    The latest batch in ``ActiveDemonstration.batches_history`` is by
    convention the one the LLM is reasoning about on a
    TOOL_BATCH_COMPLETED wake (its results just landed) or a
    user/system wake while in_flight (it's still running).
    """

    batch_id: str
    batch_index: int  # 1-based, in dispatch order within this demo
    dispatched_at: datetime
    invocations: list[InvocationHistoryEntry] = field(default_factory=list)


@dataclass
class ActiveDemonstration:
    """Tracks the demonstration the agent is currently working through.

    A demonstration is one coherent goal — e.g. "show me how to create
    a task". It can span many inference turns (each turn fires a
    parallel batch of tool calls, the next turn sees results and
    decides the next batch) and only ends when the LLM either says
    ``demonstration_action='end_current'`` or ``start_new`` (which
    interrupts and replaces this one), OR when one of the runaway-cost
    guardrails (retry-cap / batch-ceiling) trips.

    Fields:
      * ``batches_history``: ordered list of :class:`BatchHistoryEntry`,
        one per dispatched batch. Each entry holds every invocation in
        that batch (parallel) with its current status + result. This is
        the structured view the per-round state-context block renders
        for the LLM. The latest entry is the batch currently being
        reasoned about (in flight or just resolved).
      * ``tool_batches_dispatched``: counter — incremented every time a
        batch under this demo is *actually dispatched* (declined /
        replaced batches don't count). Enforced against
        ``MAX_TOOL_BATCHES_PER_DEMONSTRATION``. Equal to
        ``len(batches_history)``.
    """

    id: str
    name: str
    started_at: datetime
    batches_history: list[BatchHistoryEntry] = field(default_factory=list)
    tool_batches_dispatched: int = 0


@dataclass
class InFlightBatch:
    """A batch that's been dispatched and is awaiting tool results,
    OR has results in but whose TOOL_BATCH_COMPLETED inference has
    not yet successfully consumed them.

    A batch leaves "in flight" only when the LLM's TOOL_BATCH_COMPLETED
    inference *succeeds* (parses cleanly and we act on it). Until then,
    even if every per-tool result is in hand, the batch is still
    in-flight and the two-trigger rule blocks new tool calls except
    via ``start_new``.

    ``batch_id`` is the per-batch identity that ties the dispatch's
    ``tool_call_batch`` server message to its eventual TOOL_BATCH_COMPLETED
    consumer. The processor uses it to drop late results from a batch
    that has already been finalized (timeout / cancellation / consumed).

    ``tool_names`` carries the names of every tool in the batch (in the
    order they were proposed). Used by the TOOL_BATCH_COMPLETED wake
    reason so the next inference's wake message tells the LLM exactly
    which tools just finished, rather than relying on it to chase
    call_ids back through history.
    """

    demo_id: str
    batch_id: str
    expected_size: int
    dispatched_at: datetime
    tool_names: list[str] = field(default_factory=list)
    timeout_task: Optional[asyncio.Task] = None
    inference_attempts: int = 0


@dataclass
class PendingConfirmationBatch:
    """A batch the LLM proposed where ≥1 tool is marked
    ``requires_confirmation=true``. Held server-side, NOT dispatched,
    until the visitor's reply resolves it.

    Carries its own ``batch_id`` (minted at proposal time) so a
    proposed-but-not-yet-dispatched batch can still be referenced
    consistently in logs and bookkeeping. On accept, the same batch_id
    transitions onto the resulting :class:`InFlightBatch`.

    ``openai_tool_calls`` and ``internal_tool_calls`` are the
    pre-rendered structures we'll dispatch on accept. ``announce_speech``
    is the LLM's permission-asking utterance from the round it was
    proposed (already pushed downstream — kept here for traceability).
    """

    demo_id: str
    batch_id: str
    openai_tool_calls: list[dict]
    internal_tool_calls: list[dict]
    confirmable_tool_names: list[str]
    proposed_at: datetime
    announce_speech: str = ""


class InAppAgentProcessor(FrameProcessor):
    """Top-level frame processor for the in-app bot."""

    def __init__(
        self,
        *,
        rtvi: RTVIProcessor,
        user_aggregator: LLMUserAggregator,
        assistant_aggregator: LLMAssistantAggregator,
        runtime_config: InAppRuntimeConfig,
        output_language: str = "English",
        language_code: str = "en",
        kickoff_received_event: Optional[asyncio.Event] = None,
    ) -> None:
        super().__init__()
        self._rtvi = rtvi
        self._user_aggregator = user_aggregator
        self._assistant_aggregator = assistant_aggregator
        self._config = runtime_config
        self._kickoff_received_event = kickoff_received_event
        # Session analytics — populated as the conversation flows, then
        # snapshotted at session end via :meth:`get_session_data`. OSS
        # Voqi doesn't POST these anywhere by default; wire your own
        # analytics sink into bot.py's outer ``finally`` if you want
        # them. Kept on the processor (not a side channel) so the data
        # is always live with the in-flight conversation state.
        self._session_started_at: Optional[datetime] = None
        self._session_transcript: list[dict] = []
        self._session_tool_call_log: list[dict] = []
        self._session_message_count = 0
        self._session_tool_call_count = 0
        self._output_language = output_language or "English"
        # ISO language code (matches frontend's SUPPORTED_LANGUAGES). Used
        # by :meth:`_speak_canned` to render translated apology strings
        # when the LLM itself is unavailable — see
        # :mod:`brain.canned_speech`.
        self._language_code = language_code or "en"
        # Per-instance tunables. Default to the module-level constants
        # (driven by IN_APP_* env vars), but rebound per-processor
        # so concurrent test scenarios mutating them via the harness
        # can't corrupt each other's runtime behaviour.
        # Built-in default, optionally overridden by
        # ``max_tool_batches_per_demonstration`` from ``voqi.config.yaml``.
        self._max_tool_batches_per_demonstration = (
            runtime_config.max_tool_batches_per_demonstration
            if runtime_config.max_tool_batches_per_demonstration is not None
            else MAX_TOOL_BATCHES_PER_DEMONSTRATION
        )
        # Built-in default, optionally overridden by
        # ``batch_timeout_seconds`` from ``voqi.config.yaml``.
        self._batch_timeout_seconds = (
            runtime_config.batch_timeout_seconds
            if runtime_config.batch_timeout_seconds is not None
            else BATCH_TIMEOUT_SECONDS
        )
        self._heartbeat_timeout_seconds = HEARTBEAT_TIMEOUT_SECONDS
        self._reply_watchdog_seconds = REPLY_WATCHDOG_SECONDS
        self._inference_retry_limit = INFERENCE_RETRY_LIMIT
        self._idle_warning_after_seconds = IDLE_WARNING_AFTER_SECONDS
        self._idle_end_after_warning_seconds = IDLE_END_AFTER_WARNING_SECONDS
        self._screenshot_timeout_seconds = SCREENSHOT_TIMEOUT_SECONDS

        # Screenshot broker. Every LLM inference asks the widget for a
        # snapshot of its host page; the bytes are attached to the
        # human-marker message as an ``image_url`` content block. The
        # service is purely a request/response broker — sender is our
        # ``_emit_to_widget`` (RTVI server-message dispatch); inbound
        # ``screenshot_response`` arrives in
        # :meth:`_setup_rtvi_handlers` and routes to ``resolve``.
        self._screenshot_service = ScreenshotService(
            sender=self._emit_to_widget,
            default_timeout_seconds=self._screenshot_timeout_seconds,
        )

        # Idle-session machinery (event-driven cancel+rearm). The
        # warning task sleeps for ``_idle_warning_after_seconds`` then
        # fires the SYSTEM "are you still there?" wake; if it fires,
        # it spawns the end task which sleeps for
        # ``_idle_end_after_warning_seconds`` and force-ends the
        # session. Either task can be cancelled at any time by
        # :meth:`_cancel_idle_timer` (called on interruption, on new
        # qualifying input, and any other event that means the
        # visitor is engaged).
        self._idle_warning_task: Optional[asyncio.Task] = None
        self._idle_end_task: Optional[asyncio.Task] = None

        # Session-cap heads-up watchdog. Fires once at the 80-minute
        # mark (10 minutes before the 90-minute hard cap) so the
        # visitor gets a polite warning that the session is about to
        # close. See ``_session_cap_warning_runner``.
        self._session_cap_warning_task: Optional[asyncio.Task] = None

        # ── LLM + history ──────────────────────────────────────────────
        # Single LangChain ChatOpenAI client; per-round we wrap it with
        # ``with_structured_output`` against a schema built for the
        # current wake reason (see :func:`build_in_app_schema`).
        # Built-in model default, optionally overridden by ``llm_model:``
        # in ``voqi.config.yaml``.
        self._llm = ChatOpenAI(
            model=runtime_config.llm_model or OPENAI_MODEL,
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        # InAppConversationHistory still owns the rolling user /
        # assistant message log + summarization. Its content is rendered
        # INSIDE the single agent-turn template now, not passed to the
        # LLM as a separate message list — see :meth:`_render_agent_turn_prompt`.
        # The ``system_message`` field on history is unused by inference;
        # we pass an empty string for it.
        self._history = InAppConversationHistory(system_message="")

        # Instrument the history's central append() so every user /
        # assistant message also lands in the session-analytics
        # transcript. One choke-point captures all 8 call sites
        # (append_user, append_assistant_text, append_assistant_tool_calls,
        # append_tool_result) without scattering tracking calls.
        _orig_history_append = self._history.append

        def _instrumented_history_append(message: dict) -> None:
            _orig_history_append(message)
            self._record_message_for_analytics(message)

        self._history.append = _instrumented_history_append  # type: ignore[method-assign]

        # ── Tool dispatch (parallel, demonstration-tagged) ─────────────
        # No per-tool timeout — the batch-timeout handler is the single
        # source of truth for "tool took too long" (sleeps
        # ``_batch_timeout_seconds`` then cancels every still-pending
        # call in the batch and synthesises ``error: "timeout"``
        # payloads).
        self._tool_dispatcher = ToolDispatcher(
            on_outcome=self._on_tool_outcome,
        )
        # ── Three-state demonstration / batch machine ──────────────────
        # Active demonstration. None when there is no demonstration in
        # flight. Lifecycle is LLM-driven via the ``demonstration_action``
        # field of :class:`InAppAgentOutput`:
        #   start_new   → cancel any prior + create a fresh one with name
        #   end_current → cancel pending tools + clear (no replacement)
        #   continue    → leave alone
        self._active_demonstration: Optional[ActiveDemonstration] = None
        # The currently-dispatched batch (waiting on results OR on the
        # TOOL_BATCH_COMPLETED inference to consume them). Mutually
        # exclusive with ``_pending_confirmation_batch``.
        self._in_flight_batch: Optional[InFlightBatch] = None
        # A batch the LLM proposed that includes one or more tools
        # requiring confirmation. Held until the visitor's next reply
        # resolves it (accept / decline / replace / keep_waiting), or
        # the demo is interrupted / ended.
        self._pending_confirmation_batch: Optional[PendingConfirmationBatch] = None
        # Tracks the per-demo tool batch result accumulator. Maps
        # demo_id → {call_id → outcome dict}. When all calls in a batch
        # have outcomes, the batch is enqueued as TOOL_BATCH_COMPLETED.
        self._batch_pending: dict[str, dict[str, dict]] = {}
        self._batch_expected_size: dict[str, int] = {}
        # call_id → tool name (so :meth:`_on_tool_outcome` can write
        # the result into the active demo's tool_invocations_log without
        # having to re-look up the name from history).
        self._tool_call_names: dict[str, str] = {}
        self._tool_call_args: dict[str, dict] = {}
        # Flipped to True the moment the FIRST speech token of the
        # first inference is pushed downstream. We then emit a custom
        # ``agent_ready`` RTVI server message so the widget knows the
        # agent is actually producing output (not just that the
        # pipeline is wired up — Pipecat's built-in BotReady fires
        # before any LLM call has happened, which is too early for a
        # "say something now" UX).
        self._agent_ready_sent: bool = False

        # ── Priority queue + processor task ────────────────────────────
        self._wake_queue: asyncio.PriorityQueue[RankedEnvelope] = asyncio.PriorityQueue()
        self._pump_task: Optional[asyncio.Task] = None
        self._processing_blocked = False
        self._processing_event = asyncio.Event()
        # Frame currently being acted on by the processor — used by
        # interrupt handling to decide whether to put it back.
        self._frame_in_flight: Optional[RankedEnvelope] = None

        # ── Background tasks ───────────────────────────────────────────
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_heartbeat_time: Optional[float] = None
        self._session_started: bool = False

        self._reply_watchdog_task: Optional[asyncio.Task] = None
        self._reply_watchdog_signal: Optional[asyncio.Event] = None

        # ── Incomplete-prompt turn detection ───────────────────────────
        # The LLM still classifies user turns as
        # complete / incomplete_short / incomplete_long via the
        # ``user_turn_status`` field. The post-classification follow-up
        # TIMER (which used to nudge the agent to say "still with me?"
        # after N seconds of silence) has been removed. The widget is
        # a passive listener on a website, not an active call — if
        # the visitor trails off mid-clause we just stay quiet and
        # let them resume on their own. The classification still
        # gates output suppression for that turn; nothing schedules
        # a follow-up.

        # ── Lifecycle ──────────────────────────────────────────────────
        self._stopped = False
        self._ended = False
        self._processing_message = False
        self._finalizing_turn = False
        # Set to True by ``_cancel_pump_task`` BEFORE the message-pump
        # task is cancelled so any LangChain ``astream`` call running
        # inside it knows to abort even if the stream completed before
        # the cancel signal had a chance to interrupt the underlying
        # network IO. The streaming round (:meth:`_llm_round_streaming`)
        # checks this flag after the loop exits and raises
        # ``CancelledError`` so a stale half-buffered response never
        # reaches downstream processing.
        self._cancelling_current_response_generation = False

        # ── RTVI hook + aggregator turn handlers ───────────────────────
        self._setup_rtvi_handlers()
        self._wire_aggregator_turn_handlers()

    # ==================================================================
    # Frame entrypoint
    # ==================================================================

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self._start(frame)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (EndFrame, CancelFrame)):
            self._ended = True
            await self._stop()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            await self._handle_interruption()
            await self.push_frame(frame, direction)
            return

        # User message from STT aggregator
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            text = self._extract_latest_user_text(frame)
            if text:
                await self._handle_user_input(text)
            return

        # Custom kickoff frame (sent once when the widget connects)
        from adapters.frames import SessionOpenFrame  # local import — frame is internal
        if isinstance(frame, SessionOpenFrame):
            logger.info("[in-app] received SessionOpenFrame")
            if self._kickoff_received_event is not None:
                self._kickoff_received_event.set()
            self._session_started = True
            self._last_heartbeat_time = time.time()
            self._create_reply_watchdog()
            self._enqueue(
                priority=1,
                frame=InAppMessageFrame(
                    message="",
                    message_type=MessageType.KICKOFF,
                    put_back_when_interrupted=False,
                ),
            )
            self._create_pump_task()
            return

        await self.push_frame(frame, direction)

    # ==================================================================
    # User input
    # ==================================================================

    async def _handle_user_input(self, text: str) -> None:
        """Route a fresh user utterance into the priority queue.

        No pre-inference classification: the same LangChain
        ``with_structured_output`` call that produces the spoken reply
        also returns ``user_turn_status`` and ``demonstration_action`` (see
        :class:`brain.agent_output.InAppAgentOutput`),
        so the completeness verdict and the demonstration-switch
        decision land in the same round-trip.
        """
        if self._processing_message:
            return
        self._processing_message = True
        try:
            user_message = text.strip()
            if not user_message:
                return
            logger.info(f"[in-app] user: {user_message}")
            await self._emit_to_widget({"type": "user_message", "text": user_message})
            # NOTE: do NOT reset the idle timer here — voice is gated
            # on the relevance filter. The bump happens inside
            # ``_run_one_round`` after the LLM confirms the audio was
            # actually addressed to the agent.
 
            # Feed the text turn-stop strategy. It reads this rolling
            # buffer to decide whether a future utterance has actually
            # ended; without an entry here it would never settle.

            # USER_MESSAGE preempts pending tool-result batches by
            # priority. Whether it cancels the in-flight demonstration
            # is up to the LLM — see the [DEMO: ...] tag handling in
            # :meth:`_run_one_round`.
            self._create_reply_watchdog()
            self._enqueue(
                priority=1,
                frame=InAppMessageFrame(
                    message=user_message,
                    message_type=MessageType.USER_MESSAGE,
                    put_back_when_interrupted=False,
                ),
            )
            self._create_pump_task()
        finally:
            self._processing_message = False

    # ==================================================================
    # Agent turn — the unified handler for everything pulled off the queue
    # ==================================================================

    async def _process_message(self, frame: InAppMessageFrame) -> None:
        """Route one queue entry to the right handler.

        Stale-result discard: a tool-related frame whose
        ``demonstration_id`` no longer matches the active demo is
        silently dropped. That happens when the user redirected mid-demo
        — the in-flight tool tasks were cancelled at redirect time, but
        a few results may have already landed before cancel ran.
        """
        if frame.message_type in (
            MessageType.TOOL_BATCH_COMPLETED,
            MessageType.TOOL_RESULT,
            MessageType.TOOL_FAILED,
        ):
            if (
                frame.demonstration_id is not None
                and frame.demonstration_id != self._current_demonstration_id
            ):
                logger.info(
                    f"[in-app] dropping stale result from demo "
                    f"{frame.demonstration_id} (current={self._current_demonstration_id})"
                )
                return

        # Pause unconditionally — every handler below produces a turn
        # of speech (LLM round OR canned utterance) that pushes
        # LLMFullResponseStart/End frames; the assistant aggregator's
        # ``on_assistant_turn_stopped`` event resumes the pump on the
        # End frame. This is the single pause/resume contract for
        # every visitor-facing utterance.
        self._pause_processing()
        if frame.message_type == MessageType.CANNED_SPEECH:
            await self._handle_canned_speech(frame)
        else:
            await self._handle_agent_turn(frame)

    async def _handle_agent_turn(self, frame: InAppMessageFrame) -> None:
        """Unified LLM-call gate. Wakes are one of:

        * USER_MESSAGE / TEXT_MESSAGE — fresh user input. The schema
          allows demonstration_action transitions (continue/start_new/
          end_current); ``tool_invocations`` is exposed only with
          start_new (so the user can interrupt). On a pending-
          confirmation state, the schema also requires a resolution.
        * TOOL_BATCH_COMPLETED — the previous batch's results landed.
          This is the *only* non-user wake that's allowed to dispatch
          new tools (the second leg of the two-trigger rule).
        * KICKOFF / SYSTEM — synthetic nudge. ``tool_invocations`` not exposed.

        Any state-mutation (demo switch, batch resolution, batch
        dispatch) happens inside ``_run_one_round`` based on the parsed
        :class:`InAppAgentOutput`.
        """
        # Stale TOOL_BATCH_COMPLETED guard: the frame carries the
        # batch_id of the batch whose results it describes. If the
        # in-flight batch has rotated since (different batch_id, or no
        # in-flight batch at all because it was cancelled / consumed),
        # this frame is a leftover. Drop it without running inference.
        if frame.message_type == MessageType.TOOL_BATCH_COMPLETED:
            frame_batch_id = (frame.data or {}).get("batch_id")
            in_flight_id = (
                self._in_flight_batch.batch_id
                if self._in_flight_batch is not None
                else None
            )
            if frame_batch_id != in_flight_id:
                logger.info(
                    f"[in-app] dropping stale TOOL_BATCH_COMPLETED for "
                    f"batch={frame_batch_id[:8] if frame_batch_id else '?'} "
                    f"(in-flight={in_flight_id[:8] if in_flight_id else 'none'})"
                )
                return

        # Append USER turns to conversation history. Other wakes
        # (KICKOFF / SYSTEM / TOOL_BATCH_COMPLETED) do NOT enter
        # history — the template's "Current turn state" + "Wake reason"
        # sections render the wake context inline for THIS round, and
        # past wakes' effects are already encoded in the assistant
        # turns + batch history that follow them.
        logger.info(f"[in-app] Message to process: type={frame.message_type}, content={frame.message}")
        if frame.message_type in (MessageType.USER_MESSAGE, MessageType.TEXT_MESSAGE):
            self._history.append_user(frame.message)

        if frame.message_type == MessageType.USER_MESSAGE:
            wake_mode = "user_voice"
        elif frame.message_type == MessageType.TEXT_MESSAGE:
            wake_mode = "user_text"
        elif frame.message_type == MessageType.TOOL_BATCH_COMPLETED:
            wake_mode = "tool_batch_completed"
        elif frame.message_type == MessageType.KICKOFF:
            wake_mode = "kickoff"
        elif frame.message_type == MessageType.SCREENSHOT_RESULT:
            # The agent asked for a screenshot on a prior turn (set
            # decision_to_request_screenshot=true). The widget canvased
            # its DOM and shipped the bytes back; we built this frame
            # in _fetch_screenshot_and_enqueue with both the image AND
            # the context that triggered the request. Wake mode tells
            # the prompt builder to render the screenshot as an
            # image_url content block plus the original visitor
            # utterance so the LLM has the full picture.
            wake_mode = "screenshot_result"
        else:
            # No other message types should reach _run_one_round —
            # canned-speech frames are handled by _handle_canned_speech
            # before this branch. Anything else is a bug.
            logger.error(
                f"[in-app] unexpected message type "
                f"{frame.message_type!r} reached _process_message "
                "round-dispatch — dropping"
            )
            return

        # Defensive guard: a tool_batch_completed wake means tools
        # were just in flight, which means batch_state was in_flight
        # and no idle timer should have been armed. If somehow one IS
        # armed (state corruption / race / a bug we haven't found),
        # cancel it before processing — the visitor was effectively
        # active during the batch's execution; firing a "still there?"
        # check-in right after a tool completes would be jarring and
        # incorrect. Logged loudly so we notice if it ever fires in
        # production.
        if (
            wake_mode == "tool_batch_completed"
            and self._is_any_idle_timer_armed()
        ):
            logger.warning(
                "[in-app] tool_batch_completed wake but an idle "
                "timer was armed (corrupt-state guard) — cancelling "
                "defensively before processing"
            )
            await self._cancel_idle_timer()

        # ── Guide-mode user wake: fetch screenshot inline, then run ──
        # Guide mode runs ONE inference per visitor turn — and that
        # inference always sees a fresh screenshot. We do the whole
        # thing inline here: await the screenshot bytes, build a
        # synthetic SCREENSHOT_RESULT wake frame carrying the
        # captured image + the visitor's utterance as the brief, and
        # call ``_run_one_round`` directly. No background task, no
        # second pass through the priority queue, no SCREENSHOT_RESULT
        # frame enqueue/dequeue dance. The action-mode
        # ``decision_to_request_screenshot`` path still uses the
        # queued flow (``_fetch_screenshot_and_enqueue``) because in
        # action mode the LLM has to ack first, and the second-stage
        # inference is a separate logical turn. Guide mode has no
        # such two-stage decision — one fetch, one inference, one
        # turn.
        if (
            self._config.mode == "guide"
            and wake_mode in ("user_voice", "user_text")
        ):
            utterance = (frame.message or "").strip()
            request_context = utterance or (
                "Visitor sent input but the transcript was empty — answer "
                "from the screenshot if a clear ask is visible, otherwise "
                "ask them to say more."
            )

            # Inline screenshot fetch. ``CancelledError`` propagates
            # naturally — interruption / shutdown cancels the await
            # along with the surrounding pump task.
            roundtrip_started_at = time.monotonic()
            logger.info(
                "[in-app] 📸 guide-mode screenshot request — "
                "awaiting widget (timeout=15.0s)"
            )
            captured: Optional[CapturedScreenshot] = None
            capture_failed = False
            capture_error: Optional[str] = None
            try:
                captured = await self._screenshot_service.request(
                    timeout=15.0,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                elapsed_ms = (time.monotonic() - roundtrip_started_at) * 1000
                logger.warning(
                    f"[in-app] 📸 guide-mode screenshot fetch errored "
                    f"after {elapsed_ms:.0f}ms: {e} — letting LLM "
                    "apologise contextually"
                )
                capture_failed = True
                capture_error = f"sender_error: {e}"
            if captured is None and not capture_failed:
                elapsed_ms = (time.monotonic() - roundtrip_started_at) * 1000
                logger.info(
                    f"[in-app] 📸 guide-mode screenshot returned no "
                    f"bytes after {elapsed_ms:.0f}ms — letting LLM "
                    "apologise contextually"
                )
                capture_failed = True
                capture_error = "timeout_or_widget_error"

            # Build the wake frame ``_run_one_round`` /
            # ``_llm_round_streaming`` already know how to consume on
            # screenshot_result wakes — same shape the action-mode
            # ``_fetch_screenshot_and_enqueue`` produces, just built
            # inline rather than enqueued.
            data: dict = {
                "screenshot_request_context": request_context,
                "capture_failed": capture_failed,
                "original_wake_mode": wake_mode,
            }
            if capture_failed:
                data["capture_error"] = capture_error
            else:
                assert captured is not None
                data["captured_screenshot"] = {
                    "image_b64": captured.image_b64,
                    "mime": captured.mime,
                    "width": captured.width,
                    "height": captured.height,
                    "request_id": captured.request_id,
                    "elapsed_ms": captured.elapsed_ms,
                }
            synthetic_frame = InAppMessageFrame(
                message=request_context,
                message_type=MessageType.SCREENSHOT_RESULT,
                # Inline path — never queued, so the put-back-on-
                # interrupt flag has no effect here. Set False so the
                # frame's intent matches its lifetime.
                put_back_when_interrupted=False,
                data=data,
            )
            if not capture_failed:
                assert captured is not None
                total_ms = (time.monotonic() - roundtrip_started_at) * 1000
                logger.info(
                    f"[in-app] 📸 guide-mode screenshot received in "
                    f"{captured.elapsed_ms:.0f}ms (total round-trip "
                    f"{total_ms:.0f}ms) — running LLM round inline "
                    f"(brief: {len(request_context)} chars)"
                )

            try:
                was_valid_user_turn = await self._run_one_round(
                    wake_mode="screenshot_result",
                    wake_frame=synthetic_frame,
                )
            except asyncio.CancelledError:
                raise

            # Same idle-timer post-round bookkeeping as the action-mode
            # path below. Guide mode treats a relevant + complete
            # screenshot_result round as a valid user turn (set by
            # ``_run_guide_mode_round``); off_topic / incomplete
            # return False and the timer doesn't reset.
            if was_valid_user_turn:
                await self._cancel_idle_timer()
            await self._arm_idle_timer_if_appropriate()
            return

        try:
            was_valid_user_turn = await self._run_one_round(
                wake_mode=wake_mode, wake_frame=frame,
            )
        except asyncio.CancelledError:
            # Pump was cancelled mid-round (interruption etc.). Don't
            # touch the idle timer — interruption alone never cancels
            # it (see _handle_interruption); only valid input does.
            raise

        # ── Idle-timer orchestration ──────────────────────────────────
        # Two independent concerns, applied in order. Keeping them
        # separate makes the rule self-evident: "valid input always
        # cancels" and "arm-if-conditions-hold" are different things
        # and shouldn't be entangled.
        #
        # Concern 1 — VALID INPUT CANCELS the timer.
        #   A valid user turn is text (always) or voice classified
        #   `relevant`. Either way, the visitor is engaged and the
        #   running timer (whether stage 1 or stage 2) is moot.
        if was_valid_user_turn:
            await self._cancel_idle_timer()

        # Concern 2 — ARM stage 1 if all conditions hold.
        #   A no-op when a timer is already armed (the existing one
        #   keeps running) — so off-topic voice and idle-warning
        #   SYSTEM wakes don't disturb a pre-armed timer.
        await self._arm_idle_timer_if_appropriate()

    async def _run_one_round(
        self,
        *,
        wake_mode: Literal[
            "user_voice", "user_text", "tool_batch_completed",
            "kickoff", "screenshot_result",
        ],
        wake_frame: Optional[InAppMessageFrame] = None,
    ) -> bool:
        """Fire one structured-output LLM call and act on its decision.

        Branch order is significant — checked top-down so each layer
        can short-circuit:

          1. Incomplete user turn → start timeout, return.
          2. Pending batch resolution (when one is awaiting confirmation):
             accept / decline / replace / keep_waiting.
          3. Demonstration action: start_new (cancels everything),
             end_current (cancels everything, no replacement), continue.
          4. Tool dispatch — gated by the two-trigger rule and the
             per-demo batch ceiling.
          5. Plain text reply — log + end the turn.
        """
        batch_state = self._compute_batch_state()
        logger.info(
            f"[in-app] round start wake={wake_mode} "
            f"batch_state={batch_state} "
            f"demo={self._current_demonstration_id or 'none'}"
        )

        try:
            output = await self._llm_round_streaming(
                wake_mode=wake_mode,
                batch_state=batch_state,
                wake_frame=wake_frame,
            )
        except Exception as e:
            logger.exception(f"[in-app] LLM round failed: {e}")
            await self._handle_inference_failure(wake_mode=wake_mode, wake_frame=wake_frame)
            # Text is ALWAYS a valid user turn — the visitor explicitly
            # typed something at us, the LLM's failure to think about
            # it doesn't change that. Voice we can't verify post-hoc
            # (no completeness or relevance verdict), so we don't
            # claim validity.
            return wake_mode == "user_text"

        # ── Guide-mode short-circuit on screenshot_result ─────────────
        # Guide mode skips all action-mode bookkeeping (no demos, no
        # tool invocations, no pending-confirmation state machine).
        # The output the LLM produced is just (speech, point_to) plus
        # voice gates if the original turn was voice. Apply gates,
        # record speech to history, dispatch the cursor, return.
        # Kickoff is shared with action mode — speech-only, no gates,
        # no point_to in its schema — so it falls through to the
        # plain-text-reply tail below, same as action mode.
        if (
            self._config.mode == "guide"
            and wake_mode == "screenshot_result"
        ):
            return await self._run_guide_mode_round(
                output=output, wake_frame=wake_frame,
            )

        # ──────────────────────────────────────────────────────────────
        # PRIORITY ORDER for what to do with this round's output.
        # Strict: each gate short-circuits before the next runs.
        #   1. INCOMPLETENESS  (voice only, top priority).
        #      An incomplete utterance is the visitor still talking.
        #      Start the incomplete timeout, return — do NOT check
        #      relevance, do NOT touch demo state, do NOT count as a
        #      valid turn for the idle timer (visitor hasn't said
        #      anything yet).
        #   2. RELEVANCE       (voice only, complete only).
        #      A complete utterance the LLM judged not addressed to
        #      us. Suppress everything, return — do NOT count as a
        #      valid turn.
        #   3. ACTION HANDLING (only when complete + relevant, OR
        #      a non-voice wake whose semantics differ).
        #      Stage-2 end_session, demo action, pending resolution,
        #      tool dispatch, plain text reply.
        # ──────────────────────────────────────────────────────────────

        # ── Priority 1: INCOMPLETE — brief speech, no actions ──────────
        # Visitor's utterance came back as either incomplete_short
        # (paused mid-clause) or incomplete_long (trailed off). The
        # prompt asks for a short, warm nudge in BOTH cases — the
        # visitor needs feedback that we registered the start of
        # their thought and are waiting on the rest. Speech has
        # already been streamed by ``_llm_round_streaming``; here we
        # record it to conversation history so the next round has
        # context, then bail before any action handling runs.
        if output.user_turn_status in ("incomplete_short", "incomplete_long"):
            speech = output.speech.strip() if output.speech else ""
            if speech:
                self._history.append_assistant_text(speech)
            logger.info(
                f"[in-app] LLM classified user turn as "
                f"{output.user_turn_status} — spoke a nudge "
                f"({len(speech)} chars), no actions taken"
            )
            return False

        # ── Priority 2: OFF_TOPIC (voice only) — brief speech, no actions
        # The mic is passive; voice may be addressed elsewhere. The
        # prompt asks for a brief warm acknowledgment so the visitor
        # knows we heard but understood it wasn't directed at us. Same
        # treatment as incomplete_long: record speech to history,
        # suppress action handling, return False so the idle timer
        # is unaffected.
        if wake_mode == "user_voice" and output.is_message_relevant == "off_topic":
            speech = output.speech.strip() if output.speech else ""
            if speech:
                self._history.append_assistant_text(speech)
            logger.info(
                "[in-app] voice classified off_topic "
                f"— spoke a brief acknowledgment ({len(speech)} chars), no actions"
            )
            return False

        # ── Priority 2b: SCREENSHOT REQUEST — speak intent, no actions, fetch async ──
        # The LLM saw an ambiguous reference to on-screen content and
        # set ``decision_to_request_screenshot=true``. The schema and
        # prompt constrain the rest of this turn to a brief
        # acknowledgment ("let me take a look at your screen"); now we
        # honor that by:
        #   1. Recording the spoken acknowledgment to history.
        #   2. Spawning a background task that calls the screenshot
        #      service, waits for the widget to canvas + ship the
        #      bytes, and enqueues a SCREENSHOT_RESULT frame with the
        #      image PLUS the textual context that prompted the
        #      request (the visitor's utterance + the agent's
        #      acknowledgment). Frame is put_back_when_interrupted=True
        #      so a mid-fetch interruption doesn't lose the in-flight
        #      bytes — the widget already canvased them, we just
        #      hadn't fed them to the LLM yet.
        # This counts as a valid user turn (cancels idle timer) since
        # the visitor's input has been classified as relevant + complete.
        if (
            wake_mode in ("user_voice", "user_text")
            and bool(output.decision_to_request_screenshot)
        ):
            speech = output.speech.strip() if output.speech else ""
            if speech:
                self._history.append_assistant_text(speech)
            # The LLM is required to write a self-contained brief in
            # ``screenshot_request_context`` whenever it sets
            # decision_to_request_screenshot=true. Defensive fallback:
            # if the model forgot, use the raw visitor utterance so
            # the next turn at least has SOMETHING to anchor on.
            request_context = (
                (output.screenshot_request_context or "").strip()
            )
            if not request_context:
                fallback_utterance = (
                    wake_frame.message
                    if wake_frame is not None and wake_frame.message
                    else ""
                ).strip()
                logger.warning(
                    "[in-app] LLM set decision_to_request_screenshot=true "
                    "but screenshot_request_context is empty — falling back "
                    f"to raw visitor utterance ({len(fallback_utterance)} chars)"
                )
                request_context = fallback_utterance or (
                    "Visitor referred to something on screen but no "
                    "specific context was captured."
                )
            self.create_task(
                self._fetch_screenshot_and_enqueue(
                    screenshot_request_context=request_context,
                ),
                "in-app-screenshot-fetch",
            )
            logger.info(
                "[in-app] LLM set decision_to_request_screenshot=true "
                f"— spoke acknowledgment ({len(speech)} chars), brief "
                f"context ({len(request_context)} chars), spawned "
                "background fetch; no other actions on this turn"
            )
            return True  # valid user turn — idle timer cancels

        # If we got here, the round produced an actionable verdict:
        # either a user turn that is complete + (text bypasses /
        # voice judged relevant), OR a tool / system wake (which has
        # no completeness or relevance gate). User wakes count as
        # valid for the idle-timer cancel rule; tool / system wakes
        # don't (those aren't visitor activity).
        was_valid_user_turn = wake_mode in ("user_voice", "user_text")

        # ── Priority 3a: STAGE-2 END_SESSION ──────────────────────────
        # Visitor responded to the "are you still there?" check-in
        # confirming they're done. Close immediately — speak the
        # one-line goodbye, cancel timers, mark _ended, push
        # CancelTaskFrame upstream. Skip the rest of the round.
        if (
            self._is_idle_stage_two_armed()
            and wake_mode in ("user_voice", "user_text")
            and output.idle_warning_resolution == "end_session"
        ):
            # The goodbye speech was already streamed token-by-token in
            # _llm_round_streaming above — pushing the frames again here
            # would double-emit the goodbye to the widget. We only need
            # the bookkeeping: rolling history + turn-detector history,
            # because the early ``return`` below skips the canonical
            # plain-text-reply tail that normally does this.
            speech = output.speech.strip() if output.speech else ""
            if speech:
                self._history.append_assistant_text(speech)
            logger.info(
                "[in-app] idle stage-2: visitor confirmed "
                "end_session — closing immediately"
            )
            await self._signal_session_ending(reason="visitor_confirmed_end")
            await self._cancel_idle_timer()
            # Mark _ended so the dispatcher's post-round arm helper
            # short-circuits — without this, _arm_idle_timer_if_appropriate
            # would race the upstream CancelTaskFrame and arm a fresh
            # stage-1 timer in the dying-session window.
            self._ended = True
            await self.push_frame(
                CancelTaskFrame(), FrameDirection.UPSTREAM
            )
            return was_valid_user_turn

        # ── Priority 3b: full action handling falls through below ────

        # If the round was a TOOL_BATCH_COMPLETED inference and parsing /
        # the LLM call succeeded above, mark the in-flight batch as
        # consumed. The schema enforces TOOL_BATCH_COMPLETED only fires
        # when a batch was in flight, so this is the natural exit point
        # for the in-flight state.
        if (
            wake_mode == "tool_batch_completed"
            and self._in_flight_batch is not None
        ):
            await self._clear_in_flight_batch()

        speech = output.speech.strip() if output.speech else ""

        # ── Demonstration action (orthogonal to pending resolution) ────
        # Defensive backstop: the schema's per-wake enum already prunes
        # invalid values, but if the LLM somehow returns one anyway
        # (model bug / future schema change), fall back to 'continue'
        # so we never start/end a demo on a wake where it doesn't
        # belong (e.g. start_new on tool_batch_completed, anything but
        # continue on system wakes).
        # screenshot_result is the action-mode second leg of a user
        # turn: the visitor's original request was deictic ("that one"),
        # the agent requested a screenshot to disambiguate, and now it
        # has the image and is acting on the visitor's original ask.
        # So the same demo-action verbs that were valid on the user
        # turn that triggered the screenshot must remain valid here —
        # otherwise start_new (a fresh demo for the now-disambiguated
        # request) gets coerced to continue and the tool dispatch is
        # subsequently dropped by the two-trigger gate. Guide-mode
        # screenshot_result rounds short-circuit above this gate via
        # _run_guide_mode_round, so this entry only affects action mode.
        allowed_actions = {
            "user_voice": {"continue", "start_new", "end_current"},
            "user_text": {"continue", "start_new", "end_current"},
            "tool_batch_completed": {"continue", "end_current"},
            "kickoff": {"continue"},
            "screenshot_result": {"continue", "start_new", "end_current"},
        }.get(wake_mode, {"continue"})
        if output.demonstration_action not in allowed_actions:
            logger.warning(
                f"[in-app] LLM emitted demonstration_action="
                f"{output.demonstration_action!r} on wake_mode={wake_mode!r} "
                f"— not in allowed set {sorted(allowed_actions)}; "
                "coercing to 'continue'"
            )
            output.demonstration_action = "continue"

        # Filler-trap backstop: on a tool_batch_completed wake the
        # binary contract is `continue` MUST queue more tools,
        # `end_current` delivers the final answer. If the LLM emits
        # `continue` with empty tool_invocations the visitor is left
        # hanging — the round ends with no further action and no
        # answer. Coerce to `end_current` so the demonstration at
        # least closes cleanly and the visitor can re-engage. The
        # speech (which the LLM thought was a "I'll get back to you"
        # progress line) gets surfaced as the final wrap-up; not
        # ideal but better than a silent hang.
        if (
            wake_mode == "tool_batch_completed"
            and output.demonstration_action == "continue"
            and not output.tool_invocations
        ):
            logger.warning(
                "[in-app] LLM emitted continue + empty tool_invocations "
                "on tool_batch_completed — filler-trap violation; coercing "
                "to end_current so the demonstration closes cleanly"
            )
            output.demonstration_action = "end_current"

        # If start_new or end_current fires, cancellation cascade
        # clears in-flight + pending + batch-timeout task.
        if output.demonstration_action == "start_new":
            name = (output.demonstration_name or "").strip() or "Untitled demonstration"
            await self._start_new_demonstration(name=name)
        elif output.demonstration_action == "end_current":
            await self._end_current_demonstration()
            # tool_invocations under end_current are nonsensical — drop them.
            if output.tool_invocations:
                logger.warning(
                    "[in-app] LLM emitted tool_invocations with "
                    "demonstration_action='end_current' — discarding"
                )
                output.tool_invocations = []

        # ── Pending-confirmation resolution ────────────────────────────
        # Only meaningful when we entered this round with a pending batch
        # AND the demonstration_action didn't already wipe it (start_new
        # / end_current cancellation cascades drop the pending batch).
        if (
            batch_state == "pending_confirmation"
            and self._pending_confirmation_batch is not None
            and output.pending_batch_resolution is not None
        ):
            await self._resolve_pending_batch(
                resolution=output.pending_batch_resolution,
                speech=speech,
                replacement_invocations=output.tool_invocations,
            )
            # _resolve_pending_batch handles its own speech / tool
            # bookkeeping. The plain-text fallback below should not run.
            return was_valid_user_turn

        # ── Tool dispatch (two-trigger rule + batch ceiling) ───────────
        if output.tool_invocations:
            allowed = self._tool_invocations_dispatch_allowed(
                wake_mode=wake_mode,
                demonstration_action=output.demonstration_action,
            )
            if not allowed:
                logger.warning(
                    f"[in-app] LLM emitted tool_invocations under wake_mode="
                    f"{wake_mode}, demonstration_action="
                    f"{output.demonstration_action} — discarding "
                    "(two-trigger rule violation)"
                )
                output.tool_invocations = []

        if output.tool_invocations:
            # Enforce per-demo batch ceiling at dispatch time.
            if (
                self._active_demonstration is not None
                and self._active_demonstration.tool_batches_dispatched
                >= self._max_tool_batches_per_demonstration
            ):
                logger.warning(
                    f"[in-app] demo '{self._active_demonstration.name}' hit "
                    f"batch ceiling ({self._max_tool_batches_per_demonstration}); "
                    "ending demonstration"
                )
                await self._force_end_demonstration_with_apology(
                    CannedKey.BATCH_CEILING_HIT
                )
                return was_valid_user_turn

            await self._dispatch_or_park_for_confirmation(
                output_tool_invocations=output.tool_invocations,
                speech=speech,
            )
            return was_valid_user_turn

        # ── Plain text reply ──────────────────────────────────────────
        if speech:
            preview = speech[:120].replace("\n", " ")
            logger.info(f"[in-app] round → plain reply: {preview!r}")
            self._history.append_assistant_text(speech)
        else:
            logger.info("[in-app] round → no speech, no action")
        return was_valid_user_turn

    async def _run_guide_mode_round(
        self,
        *,
        output: InAppAgentOutput,
        wake_frame: Optional[InAppMessageFrame],
    ) -> bool:
        """Apply a guide-mode screenshot_result round's output.

        The schema in guide mode at this wake exposes only
        ``speech`` + ``point_to`` (always) + voice-only gates
        (``is_message_relevant`` + ``user_turn_status``) when the
        original wake was a voice utterance + ``idle_warning_resolution``
        when the idle stage-2 timer is armed. There is no
        ``tool_invocations``, ``demonstration_action``, or
        ``pending_batch_resolution`` in this schema — so this handler
        is small on purpose.

        Speech itself was already streamed token-by-token to the widget
        in :meth:`_llm_round_streaming`; this method's job is the
        bookkeeping (history + turn detector) plus the cursor dispatch.
        Returns ``True`` if this turn counts as valid visitor activity
        for the idle timer (everything except ``off_topic`` voice).
        """
        speech = output.speech.strip() if output.speech else ""

        # ── Off-topic voice → speak warm ack, drop point_to ──────────
        if output.is_message_relevant == "off_topic":
            if speech:
                self._history.append_assistant_text(speech)
            logger.info(
                "[in-app] guide-mode off_topic — spoke ack "
                f"({len(speech)} chars), no cursor"
            )
            return False

        # ── Incomplete → speak nudge, drop point_to ──────────────────
        if output.user_turn_status in ("incomplete_short", "incomplete_long"):
            if speech:
                self._history.append_assistant_text(speech)
            logger.info(
                f"[in-app] guide-mode {output.user_turn_status} "
                f"— spoke nudge ({len(speech)} chars), no cursor"
            )
            return False

        # ── Stage-2 end_session → close immediately ─────────────────
        if (
            self._is_idle_stage_two_armed()
            and output.idle_warning_resolution == "end_session"
        ):
            if speech:
                self._history.append_assistant_text(speech)
            logger.info(
                "[in-app] guide-mode idle stage-2 end_session "
                "— closing immediately"
            )
            await self._signal_session_ending(reason="visitor_confirmed_end")
            await self._cancel_idle_timer()
            self._ended = True
            await self.push_frame(
                CancelTaskFrame(), FrameDirection.UPSTREAM
            )
            return True

        # ── Normal answer ────────────────────────────────────────────
        if speech:
            preview = speech[:120].replace("\n", " ")
            logger.info(f"[in-app] guide-mode answer: {preview!r}")
            self._history.append_assistant_text(speech)
        else:
            logger.info("[in-app] guide-mode answer → no speech")

        # Dispatch the cursor hint to the widget. Coordinates are
        # normalized to the screenshot the LLM saw; the widget will
        # multiply by viewport size on receipt. The label is the
        # short on-screen text floated next to the dot.
        if output.point_to is not None:
            cursor_payload = {
                "type": "guide_cursor",
                "x": output.point_to.x,
                "y": output.point_to.y,
                "label": output.point_to.label,
            }
            logger.info(
                f"[in-app] → send_server_message type=guide_cursor "
                f"x={output.point_to.x:.3f} y={output.point_to.y:.3f} "
                f"label={output.point_to.label!r}"
            )
            await self._rtvi.send_server_message(cursor_payload)
        else:
            logger.debug(
                "[in-app] guide-mode round → no point_to (chit-chat "
                "/ refusal / off-screen target)"
            )
        return True

    async def _llm_round_streaming(
        self,
        *,
        wake_mode: Literal[
            "user_voice", "user_text", "tool_batch_completed",
            "kickoff", "screenshot_result",
        ],
        batch_state: Literal["idle", "in_flight", "pending_confirmation"],
        wake_frame: Optional[InAppMessageFrame] = None,
    ) -> InAppAgentOutput:
        """One LangChain ``with_structured_output`` call, streamed.

        Returns the parsed :class:`InAppAgentOutput`. While the
        response streams, every new ``speech`` token is pushed
        downstream as an :class:`LLMTextFrame` so the widget's
        transcript fills in real-time. ``LLMFullResponseStartFrame`` /
        ``LLMFullResponseEndFrame`` bracket the streamed speech and
        also drive the assistant aggregator's
        ``on_assistant_turn_started`` / ``on_assistant_turn_stopped``
        events — which the widget receives as ``assistant_turn_started``
        / ``assistant_turn_ended`` server messages for its
        "Responding…" UI flip.

        Schema shape is conditional on ``wake_mode`` + ``batch_state``
        so the LLM literally cannot emit fields that don't apply this
        round (see :func:`build_in_app_schema`).

        We never emit ``BotStartedSpeakingFrame`` /
        ``BotStoppedSpeakingFrame`` because the bot doesn't produce
        audio in in-app mode.
        """
        # On screenshot_result wakes the screenshot bytes + the textual
        # context that prompted the request both ride on
        # ``wake_frame.data`` (built in :meth:`_fetch_screenshot_and_enqueue`).
        # We extract them here so they can flow into BOTH the system
        # prompt (via screenshot_context for the wake_mode branch) AND
        # the human marker (via captured for the image_url block).
        # Extracted BEFORE schema build so guide mode can read the
        # original-wake mode out of the payload to decide whether to
        # expose voice gates on this round's schema.
        captured: Optional[CapturedScreenshot] = None
        screenshot_context: Optional[dict] = None
        if (
            wake_mode == "screenshot_result"
            and wake_frame is not None
            and wake_frame.data
        ):
            payload = wake_frame.data
            sc_dict = payload.get("captured_screenshot")
            if isinstance(sc_dict, dict) and sc_dict.get("image_b64"):
                captured = CapturedScreenshot(
                    image_b64=sc_dict["image_b64"],
                    mime=sc_dict.get("mime", "image/jpeg"),
                    width=sc_dict.get("width"),
                    height=sc_dict.get("height"),
                    request_id=sc_dict.get("request_id", ""),
                    elapsed_ms=float(sc_dict.get("elapsed_ms", 0.0)),
                )
            screenshot_context = {
                # The LLM-authored brief from the requesting turn —
                # self-contained note about what the visitor referred
                # to and what to look for in the image. This is what
                # the prompt template renders into the human-visible
                # `## Your brief` block on the screenshot_result wake.
                "request_context": payload.get(
                    "screenshot_request_context", ""
                ),
                # When the widget couldn't capture (timeout / canvas
                # error / sender failure), the prompt's screenshot_result
                # branch renders the apology guidance instead of the
                # "you have full vision now" guidance. The LLM
                # composes the apology in its own voice, calling
                # back what the brief said the request was about.
                "capture_failed": bool(payload.get("capture_failed", False)),
                "capture_error": payload.get("capture_error"),
                # Carries the wake mode that triggered this fetch so
                # guide mode at screenshot_result wakes can decide
                # whether to expose voice gates (relevance + turn
                # completeness). Action mode ignores this field — its
                # screenshot_result branch already runs without those
                # gates.
                "original_wake_mode": payload.get(
                    "original_wake_mode", "user_voice"
                ),
            }

        # Schema build — happens AFTER screenshot_context is extracted
        # so guide mode can pick up ``original_wake_mode`` to decide
        # whether voice gates apply on this round.
        schema = build_in_app_schema(
            wake_mode=wake_mode,
            batch_state=batch_state,
            tools=self._config.tools,
            idle_stage_two_armed=self._is_idle_stage_two_armed(),
            mode=self._config.mode,
            original_wake_mode=(
                screenshot_context.get("original_wake_mode")
                if screenshot_context
                else None
            ),
        )

        # Single Jinja-rendered system prompt holds EVERYTHING:
        # persona, scope, software docs, tools, current demonstration
        # state, batch-by-batch history, allowed actions for this wake,
        # turn-completeness rules, pending-confirmation rules,
        # interrupt guidance, conversation history. One place to read,
        # one place to modify. The matching short human marker just
        # kicks the LLM to act on it.
        system_prompt = self._render_agent_turn_prompt(
            wake_mode=wake_mode,
            batch_state=batch_state,
            screenshot_context=screenshot_context,
        )

        messages = _build_inference_messages(
            system_prompt=system_prompt, captured=captured,
        )

        # ── Per-turn context log ────────────────────────────────────
        # Visibility into what the LLM is being asked to decide on this
        # turn. Renders the active demo's batch history compactly so
        # the operator sees what's been called in this demonstration
        # so far + what state the agent is reasoning from.
        _demo = self._active_demonstration
        _demo_label = (
            f"{_demo.id[:8]} '{_demo.name}'" if _demo is not None else "none"
        )
        _history_summary = "[]"
        if _demo is not None and _demo.batches_history:
            _parts = []
            for _b in _demo.batches_history:
                _invs = "; ".join(
                    f"{inv.name}->{inv.status}" for inv in _b.invocations
                )
                _parts.append(f"#{_b.batch_index}({_invs})")
            _history_summary = "[" + ", ".join(_parts) + "]"
        logger.info(
            f"[in-app] inference IN  wake={wake_mode} "
            f"batch_state={batch_state} demo={_demo_label} "
            f"demo_history={_history_summary} "
            f"system_prompt_chars={len(system_prompt)}"
        )

        structured_llm = self._llm.with_structured_output(
            {
                "name": "voqi_agent_output",
                "description": (
                    "Single structured decision for the in-app agent: "
                    "speech, tool invocations, and (on user-input turns) "
                    "turn-completeness + demonstration-switch verdicts."
                ),
                "parameters": schema,
            }
        )

        emitted_speech = ""
        full_response_started = False
        latest_partial: dict = {}

        async for partial in structured_llm.astream(messages):
            if not isinstance(partial, dict):
                # Defensive: some LangChain backends yield model objects
                # at the end of the stream. Snapshot to dict for parsing.
                latest_partial = partial if isinstance(partial, dict) else {}
                continue
            latest_partial = partial

            # ── No defensive token-level gate ────────────────────────
            # All branches (complete, incomplete_short, incomplete_long,
            # off_topic) are now allowed to stream speech. The visitor
            # never being met with dead silence is more important than
            # the previous "stay quiet on invalid turn" instinct. The
            # post-stream priority gates still suppress any attempted
            # tool invocation / demo state change for invalid turns —
            # only the speech itself is allowed through here.

            cumulative_speech = partial.get("speech") or ""
            if (
                isinstance(cumulative_speech, str)
                and len(cumulative_speech) > len(emitted_speech)
            ):
                if not full_response_started:
                    await self.push_frame(LLMFullResponseStartFrame())
                    full_response_started = True
                speech_delta = cumulative_speech[len(emitted_speech):]
                emitted_speech = cumulative_speech
                # First token of the first inference of the session?
                # Fire the ``agent_ready`` notification BEFORE pushing
                # the token downstream so the widget flips into "ready"
                # state in lock-step with seeing the first text. We
                # don't gate on wake_mode (any wake that reaches first
                # speech qualifies) — the typical first wake is the
                # KICKOFF frame's greeting, but a session that starts
                # with the visitor speaking immediately would also
                # trigger here on the first user-voice round.
                if not self._agent_ready_sent:
                    self._agent_ready_sent = True
                    logger.info("[in-app] sending agent_ready (first token)")
                    try:
                        await self._rtvi.send_server_message(
                            {"type": "agent_ready"}
                        )
                    except Exception as e:  # pragma: no cover
                        logger.warning(
                            f"[in-app] failed to send agent_ready: {e}"
                        )
                await self.push_frame(LLMTextFrame(speech_delta))

        # No retract path needed anymore — every branch (off_topic,
        # incomplete_short, incomplete_long, complete) emits a
        # speech bubble that the visitor should see. The post-stream
        # priority gates handle the "no actions" suppression
        # separately. Kept as a no-op flag so the closing
        # ``if full_response_started`` block below stays
        # unconditional.
        invalid_after_stream = False
        if full_response_started:
            if invalid_after_stream:
                logger.warning(
                    "[in-app] LLM emitted speech tokens before "
                    "declaring invalid turn — pushing InterruptionFrame "
                    "to retract the partial bubble"
                )
                await self.push_frame(
                    InterruptionFrame(),
                    direction=FrameDirection.DOWNSTREAM,
                )
            else:
                await self.push_frame(LLMFullResponseEndFrame())
                # Mirror the user_message pattern: emit a single
                # canonical assistant_message at the end of the stream
                # carrying the full speech text. The widget renders
                # bubbles off this server message instead of Pipecat's
                # built-in RTVIEvent.BotTranscript (which fires
                # per-sentence and would force client-side coalescing
                # we'd rather not depend on). LLMTextFrames still flow
                # downstream for TTS — only the widget's transcript
                # rendering is rerouted.
                if emitted_speech:
                    await self._emit_to_widget({
                        "type": "assistant_message",
                        "text": emitted_speech,
                    })

        # astream cancellation backstop: if the pump task was cancelled
        # while the stream was buffering its final chunks, the cancel
        # signal might land AFTER astream already returned the full
        # payload (especially with the OpenAI streaming protocol — the
        # server can flush the entire response before our local cancel
        # propagates through httpx). In that case the natural
        # CancelledError propagation never fires and we'd act on a
        # half-stale response. Raising here ensures the round is
        # abandoned cleanly.
        if self._cancelling_current_response_generation:
            raise asyncio.CancelledError

        try:
            output = InAppAgentOutput.model_validate(latest_partial or {})
        except Exception as e:
            logger.warning(
                f"[in-app] structured output failed validation: {e} "
                f"(raw={latest_partial!r})"
            )
            output = InAppAgentOutput()

        # ── Per-turn decisions log ──────────────────────────────────
        # Mirror of the IN log — what the LLM decided this turn.
        # Pulls the speech preview out so the rest of the dict reads
        # as one line of compact decision fields.
        _out_dict = output.model_dump(exclude_none=True)
        _speech = _out_dict.pop("speech", None) or ""
        _speech_preview = _speech[:140].replace("\n", " ")
        logger.info(
            f"[in-app] inference OUT decisions={_out_dict} "
            f"speech_chars={len(_speech)} speech={_speech_preview!r}"
        )

        return output

    def _update_invocation_in_batch_history(
        self,
        *,
        batch_id: str,
        call_id: str,
        status: Literal["success", "error", "timeout", "cancelled"],
        result: Any,
    ) -> None:
        """Update the matching invocation entry in the active demo's
        batch-history record. No-op if the active demo has no entry for
        this batch_id (e.g. the demo was switched mid-flight)."""
        active = self._active_demonstration
        if active is None:
            return
        for batch in active.batches_history:
            if batch.batch_id != batch_id:
                continue
            for inv in batch.invocations:
                if inv.call_id == call_id:
                    inv.status = status
                    inv.result = result
                    return
            return

    def _speak_canned(
        self,
        key: CannedKey,
        *,
        put_back_when_interrupted: bool = False,
    ) -> None:
        """Enqueue a canned utterance to be spoken on the next pump
        cycle. Synchronous — the actual frame push happens later when
        the message processor pulls the CANNED_SPEECH frame off the
        priority queue and routes it to :meth:`_handle_canned_speech`.

        Routing every visitor-facing utterance (LLM-generated AND
        canned-recovery) through the same priority-queue → message-
        processor → handler path keeps the architecture single-pathed:
        text only ever reaches the widget by going through the pump.

        Used on emergency code paths (LLM error, retry-cap hit,
        batch-ceiling hit, response-timeout recovery). The text is
        rendered from the multilingual translation table at render
        time (in the handler) using the session's ISO language code.

        Priority 0 (above all user/tool wakes) so apologies aren't
        starved by a backlog of pending tool-result inferences.

        ``put_back_when_interrupted`` controls what happens if the
        visitor talks over this canned line mid-delivery. Default is
        ``False``: the line gets dropped — fine for most recovery
        utterances (response-timeout, idle warning, generic LLM error)
        because they're transient apologies the visitor doesn't need
        to hear in full. Set ``True`` for apologies that carry
        load-bearing meaning the visitor MUST hear — e.g. the
        end-of-demonstration apology, where dropping the line would
        leave the visitor confused about why their demo just stopped.
        """
        self._enqueue(
            priority=0,
            frame=InAppMessageFrame(
                message="",
                message_type=MessageType.CANNED_SPEECH,
                put_back_when_interrupted=put_back_when_interrupted,
                data={"canned_key": key.value},
            ),
        )

    async def _handle_canned_speech(self, frame: InAppMessageFrame) -> None:
        """Render the canned text and push the same
        ``LLMFullResponseStart`` / ``LLMText`` / ``LLMFullResponseEnd``
        frame triplet a real LLM round produces — so the assistant
        aggregator's turn lifecycle still fires (response-timeout
        notifier resets, ``_processing_event`` flips) and the widget
        gets a transcript bubble.

        Pause/resume is handled by :meth:`_process_message` (single
        contract for both LLM rounds and canned-speech turns); we
        only push the frames + record history here."""
        key_str = (frame.data or {}).get("canned_key")
        try:
            key = CannedKey(key_str)
        except (ValueError, TypeError):
            logger.warning(f"[in-app] canned-speech frame had invalid key={key_str!r}")
            return
        text = render_canned(key, self._language_code)
        if not text:
            return
        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(LLMTextFrame(text))
        await self.push_frame(LLMFullResponseEndFrame())
        # Same proactive assistant_message emit as the streaming path —
        # widget renders off this server message, not RTVIEvent.BotTranscript.
        await self._emit_to_widget({
            "type": "assistant_message",
            "text": text,
        })
        self._history.append_assistant_text(text)

    # ==================================================================
    # Demonstration lifecycle
    # ==================================================================

    @property
    def _current_demonstration_id(self) -> Optional[str]:
        """Convenience accessor for the active demo's id (or None)."""
        return (
            self._active_demonstration.id
            if self._active_demonstration is not None
            else None
        )

    async def _start_new_demonstration(self, name: str) -> str:
        """Start a fresh demonstration with the LLM-supplied name.

        If one is already active it is interrupted: pending tool tasks
        cancelled, batch bookkeeping cleared, queue purged of its
        stale frames, in-flight + pending batches dropped, batch-timeout
        task cancelled. The new demo gets a fresh id, an empty
        tool-calls log, and a zeroed batch counter.
        """
        await self._cancel_active_demonstration_state()
        new_id = ToolDispatcher.new_demonstration_id()
        self._active_demonstration = ActiveDemonstration(
            id=new_id,
            name=name.strip() or "Untitled demonstration",
            started_at=datetime.now(timezone.utc),
            batches_history=[],
            tool_batches_dispatched=0,
        )
        logger.info(
            f"[in-app] started demonstration '{self._active_demonstration.name}' "
            f"(id={new_id})"
        )
        return new_id

    async def _end_current_demonstration(self) -> None:
        """End the active demonstration without starting another.

        Pending tool tasks, in-flight batch, pending-confirmation batch,
        and the batch-timeout task are all cleared. No-op if there is no
        active demo.
        """
        if self._active_demonstration is None:
            return
        ended_name = self._active_demonstration.name
        await self._cancel_active_demonstration_state()
        logger.info(f"[in-app] ended demonstration '{ended_name}'")

    async def _force_end_demonstration_with_apology(self, key: CannedKey) -> None:
        """Enqueue a canned apology and force-end the active demo. Used
        by the runaway-cost guardrails (inference retry cap, batch
        ceiling). Safe to call when no demo is active — the canned
        speech is enqueued onto the priority queue (priority 0); the
        demo state is cleared synchronously here and the apology will
        be pushed to the visitor on the next pump cycle.

        ``put_back_when_interrupted=True`` — this apology carries the
        only signal the visitor gets that we just bailed on their
        in-flight demonstration. Dropping it on interruption would
        leave them staring at a stopped UI with no explanation, so we
        force a replay if the visitor talked over it.
        """
        await self._end_current_demonstration()
        self._speak_canned(key, put_back_when_interrupted=True)

    async def _fetch_screenshot_and_enqueue(
        self,
        *,
        screenshot_request_context: str,
        original_wake_mode: Literal["user_voice", "user_text"] = "user_voice",
    ) -> None:
        """Background task spawned when the LLM sets
        ``decision_to_request_screenshot=true``.

        Asks the widget for a screenshot via the existing
        ``ScreenshotService`` round-trip, then enqueues a
        ``SCREENSHOT_RESULT`` frame so the next inference round can
        see the image AND the LLM-produced ``screenshot_request_context``
        — its own self-contained brief about what the visitor was
        referring to and what to look for in the image.

        Why context rides on the frame: by the time the screenshot
        arrives, several other turns may have happened (interruption,
        new questions, off-topic chatter). The next time the
        screenshot frame is processed, the LLM needs to know exactly
        what this image was captured FOR. We can't rely on
        conversation history alone because the order in which
        SCREENSHOT_RESULT lands relative to the rest of the queue is
        not guaranteed (it's been pumping during the wait). The
        context the LLM wrote on the requesting turn IS the
        self-contained anchor.

        ``put_back_when_interrupted=True`` so a mid-flight
        interruption (e.g. visitor talked over the agent's "let me
        take a look") doesn't lose the bytes the widget already
        canvased + shipped — the next pump cycle will retry the
        round.

        On screenshot failure (timeout / widget capture error /
        sender error), we STILL enqueue a SCREENSHOT_RESULT frame —
        but flagged ``capture_failed=True`` with no image bytes. The
        LLM round then sees the failure inline in the prompt
        (alongside the LLM-produced context that motivated the
        request) and composes a natural, contextual apology in its
        own voice — referring back to what the visitor was asking
        about — rather than playing a canned line that doesn't know
        the topic.
        """
        capture_failed = False
        capture_error: Optional[str] = None
        # Wall-clock the round-trip from request-send to response-arrive.
        # This is observed at the bot side, so it includes:
        #   1. RTVI server-message dispatch from bot → widget,
        #   2. widget's html2canvas render of the host DOM,
        #   3. the response client-message back from widget → bot.
        # Logged on EVERY path (success, timeout, sender error) so the
        # operator can see how long each capture actually took, not
        # just the successful ones.
        roundtrip_started_at = time.monotonic()
        logger.info(
            "[in-app] 📸 screenshot request sent — awaiting widget "
            "(timeout=15.0s)"
        )
        try:
            captured = await self._screenshot_service.request(
                timeout=15.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed_ms = (time.monotonic() - roundtrip_started_at) * 1000
            logger.warning(
                f"[in-app] 📸 screenshot fetch errored after "
                f"{elapsed_ms:.0f}ms: {e} — letting LLM apologise "
                "contextually"
            )
            captured = None
            capture_failed = True
            capture_error = f"sender_error: {e}"

        if captured is None and not capture_failed:
            # Service returned None (timeout or widget reported a
            # capture error). Distinguish from a clean fetch by
            # flagging capture_failed so the LLM round renders the
            # apology branch instead of treating "no image" as
            # "everything is fine, just no picture".
            elapsed_ms = (time.monotonic() - roundtrip_started_at) * 1000
            logger.info(
                f"[in-app] 📸 screenshot request returned no bytes "
                f"after {elapsed_ms:.0f}ms (timeout / widget capture "
                "error) — letting LLM apologise contextually"
            )
            capture_failed = True
            capture_error = "timeout_or_widget_error"

        # Build the SCREENSHOT_RESULT frame. Image (when present) +
        # the LLM-produced context (always) live on .data so a
        # single deserialize at the consume site has everything the
        # next inference round needs to anchor on.
        data: dict = {
            "screenshot_request_context": screenshot_request_context,
            "capture_failed": capture_failed,
            # Carry forward the wake type that triggered this fetch.
            # Guide mode at screenshot_result wakes uses this to decide
            # whether to expose the voice relevance + completeness
            # gates: voice-originated turns need them (mic is open
            # passively), typed-originated turns don't (typed text is
            # always for you, always complete).
            "original_wake_mode": original_wake_mode,
        }
        if capture_failed:
            data["capture_error"] = capture_error
        else:
            assert captured is not None  # narrowed for type-checker
            data["captured_screenshot"] = {
                "image_b64": captured.image_b64,
                "mime": captured.mime,
                "width": captured.width,
                "height": captured.height,
                "request_id": captured.request_id,
                "elapsed_ms": captured.elapsed_ms,
            }
        # Priority 1 (same as fresh user wakes) — should preempt any
        # pending tool-result inferences. The agent has just promised
        # to look at the screen; making the visitor wait through a
        # long batch-completed round before delivering on that
        # promise would be jarring.
        self._enqueue(
            priority=1,
            frame=InAppMessageFrame(
                # ``message`` is the headline string used for log /
                # debug surfaces. The LLM-produced context is the
                # most useful one-line summary of why this frame
                # exists — surface that.
                message=screenshot_request_context,
                message_type=MessageType.SCREENSHOT_RESULT,
                put_back_when_interrupted=True,
                data=data,
            ),
        )
        if capture_failed:
            logger.info(
                "[in-app] 📸 screenshot capture FAILED — enqueued "
                "SCREENSHOT_RESULT with capture_failed=True for "
                "contextual apology"
            )
        else:
            assert captured is not None
            # Two timings worth showing:
            #   * `captured.elapsed_ms` — measured by ScreenshotService
            #     itself, from `await sender(...)` to future-resolved.
            #   * `total_ms` — measured by this method, from the call
            #     to `_screenshot_service.request(...)` until now.
            # They should be effectively identical; logging both makes
            # any divergence (e.g. a queue-ordering anomaly between the
            # service and the processor) immediately visible.
            total_ms = (time.monotonic() - roundtrip_started_at) * 1000
            logger.info(
                f"[in-app] 📸 screenshot received in "
                f"{captured.elapsed_ms:.0f}ms (total round-trip "
                f"{total_ms:.0f}ms) — enqueued SCREENSHOT_RESULT "
                f"with LLM context ({len(screenshot_request_context)} chars)"
            )


    async def _cancel_active_demonstration_state(self) -> None:
        """Internal helper: cancel pending tools for the current demo
        (if any), purge stale queue frames, drop in-flight + pending
        batches, cancel the batch-timeout task, clear bookkeeping.

        Used by ``_start_new_demonstration`` and ``_end_current_demonstration``.
        """
        previous = self._active_demonstration
        if previous is None:
            # Even with no active demo we may be holding stale batch
            # state from earlier — defensively clear it.
            await self._clear_in_flight_batch()
            self._pending_confirmation_batch = None
            return
        await self._tool_dispatcher.cancel_demonstration(previous.id)
        self._purge_queue_for_demonstration(previous.id)
        # Mark every still-``in_progress`` invocation across the demo's
        # batch history as ``cancelled`` so any session-end snapshot
        # that reads this state shows accurate terminal statuses.
        for batch in previous.batches_history:
            for inv in batch.invocations:
                if inv.status == "in_progress":
                    inv.status = "cancelled"
                    inv.result = "demonstration was interrupted before this invocation returned"
        # In-flight batch's bookkeeping is now keyed by batch_id;
        # _clear_in_flight_batch handles the per-batch dict pops.
        await self._clear_in_flight_batch()
        self._pending_confirmation_batch = None
        self._active_demonstration = None

    def _purge_queue_for_demonstration(self, demo_id: str) -> None:
        """Drain the priority queue and re-enqueue everything except
        frames tagged with ``demo_id``."""
        items_to_keep: list[RankedEnvelope] = []
        while not self._wake_queue.empty():
            try:
                item = self._wake_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            keep = True
            if item.frame is not None and item.frame.demonstration_id == demo_id:
                keep = False
            if keep:
                items_to_keep.append(item)
            self._wake_queue.task_done()
        for item in items_to_keep:
            self._wake_queue.put_nowait(item)

    # ------------------------------------------------------------------
    # Batch-state machine
    # ------------------------------------------------------------------

    def _compute_batch_state(self) -> Literal["idle", "in_flight", "pending_confirmation"]:
        """Return the current batch state. Mutually exclusive: a batch
        cannot be both in-flight and pending."""
        if self._pending_confirmation_batch is not None:
            return "pending_confirmation"
        if self._in_flight_batch is not None:
            return "in_flight"
        return "idle"

    def _tool_invocations_dispatch_allowed(
        self,
        *,
        wake_mode: str,
        demonstration_action: str,
    ) -> bool:
        """The two-trigger rule: tool dispatch is allowed only on
        TOOL_BATCH_COMPLETED wakes (the next batch under the same demo)
        or when the LLM uses ``start_new`` to interrupt and dispatch a
        fresh batch. Pending-confirmation rounds are handled separately
        via ``replace`` resolution. ``end_current`` never allows
        dispatch.
        """
        if demonstration_action == "end_current":
            return False
        if demonstration_action == "start_new":
            return True
        if wake_mode == "tool_batch_completed":
            return True
        return False

    async def _clear_in_flight_batch(self) -> None:
        """Drop the in-flight batch (cancelling its timeout task if
        armed). Also clears its accumulator state, keyed by batch_id."""
        if self._in_flight_batch is None:
            return
        ifb = self._in_flight_batch
        self._in_flight_batch = None
        if ifb.timeout_task is not None and not ifb.timeout_task.done():
            await self.cancel_task(ifb.timeout_task)
        self._batch_expected_size.pop(ifb.batch_id, None)
        self._batch_pending.pop(ifb.batch_id, None)

    async def _dispatch_or_park_for_confirmation(
        self,
        *,
        output_tool_invocations: list[InAppToolInvocation],
        speech: str,
    ) -> None:
        """Convert the LLM's structured ``tool_invocations`` into the
        OpenAI message shape, log them in conversation history, and
        either:
          * park as ``_pending_confirmation_batch`` if any tool requires
            confirmation; OR
          * dispatch immediately as the new ``_in_flight_batch`` and arm
            the batch-level timeout.

        Either way the assistant message + tool_calls (OpenAI's native
        message-protocol field) go into history right away so the LLM
        sees what it proposed on the next round. Each batch gets a fresh
        ``batch_id`` minted at proposal time so we have a stable
        identity through pending → dispatched → consumed.
        """
        # Ensure there's a demo to attribute the batch to. The two-
        # trigger rule plus the schema gating means we only get here
        # under start_new (which already minted one) or
        # tool_batch_completed (which guarantees the demo from the
        # in-flight batch survives the inference). The fallback below
        # handles any edge case where neither holds.
        if self._active_demonstration is None:
            await self._start_new_demonstration(name="Untitled demonstration")
        active = self._active_demonstration
        assert active is not None  # narrow for type-checker

        batch_id = ToolDispatcher.new_batch_id()

        openai_tool_calls = [
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": ti.name,
                    "arguments": json.dumps(ti.arguments or {}),
                },
            }
            for ti in output_tool_invocations
        ]
        internal_tool_calls = [
            {
                "id": tc_o["id"],
                "name": ti.name,
                "arguments": tc_o["function"]["arguments"],
            }
            for ti, tc_o in zip(output_tool_invocations, openai_tool_calls)
        ]

        # Determine which tools require confirmation.
        confirmable: list[str] = []
        for ti in output_tool_invocations:
            spec = self._config.find_tool(ti.name)
            if spec is not None and spec.requires_confirmation:
                confirmable.append(ti.name)

        # The agent's spoken proposal lands in history as a plain
        # assistant text message. We deliberately do NOT use OpenAI's
        # native ``assistant.tool_calls`` / ``tool`` message protocol
        # here — we drive structured output and dispatch the
        # invocations ourselves, so threading them through the OpenAI
        # tool-call wire format would only confuse the LLM (and force
        # us to ship synthetic in_progress placeholder tool messages
        # whenever a user spoke during an in-flight batch). The
        # canonical record of what was dispatched / what landed lives
        # in ``ActiveDemonstration.batches_history`` and is rendered
        # into the per-round state-context system message.
        if speech:
            self._history.append_assistant_text(speech)

        if confirmable:
            self._pending_confirmation_batch = PendingConfirmationBatch(
                demo_id=active.id,
                batch_id=batch_id,
                openai_tool_calls=openai_tool_calls,
                internal_tool_calls=internal_tool_calls,
                confirmable_tool_names=confirmable,
                proposed_at=datetime.now(timezone.utc),
                announce_speech=speech,
            )
            logger.info(
                f"[in-app] parked batch {batch_id[:8]} awaiting "
                f"confirmation: {len(internal_tool_calls)} tools, "
                f"{len(confirmable)} need confirmation "
                f"({', '.join(confirmable)})"
            )
            return

        # No confirmation needed — dispatch now.
        await self._dispatch_batch_now(
            internal_tool_calls=internal_tool_calls,
            demo_id=active.id,
            batch_id=batch_id,
        )

    async def _dispatch_batch_now(
        self,
        *,
        internal_tool_calls: list[dict],
        demo_id: str,
        batch_id: str,
    ) -> None:
        """Send the atomic ``tool_call_batch`` server message to the
        widget, wire up batch bookkeeping (keyed by ``batch_id``), spawn
        the dispatcher's per-call tasks, arm the batch-level timeout,
        and increment the per-demo batch counter.

        Order is important: speech frames have already been pushed by
        :meth:`_llm_round_streaming` (they reach the widget through
        the realtime-transcript processor). The widget-facing
        ``tool_call_batch`` server message goes out next, AFTER speech
        but BEFORE we await any dispatcher work — so the widget sees
        the speech bubble first, then receives the batch as one atomic
        unit (no half-sent batches if the bot crashes mid-loop).
        """
        active = self._active_demonstration
        if active is not None and active.id == demo_id:
            active.tool_batches_dispatched += 1

        self._batch_expected_size[batch_id] = len(internal_tool_calls)
        self._batch_pending[batch_id] = {}
        for itc in internal_tool_calls:
            self._tool_call_names[itc["id"]] = itc["name"]
            try:
                self._tool_call_args[itc["id"]] = json.loads(itc["arguments"])
            except Exception:
                self._tool_call_args[itc["id"]] = {}

        # Record the batch in the demo's structured history. Every
        # invocation enters as ``in_progress`` in dispatch order; the
        # status + result are filled in as widget results land (or as
        # the per-batch timeout fires). The state-context block reads
        # this on every round so the LLM sees a clean batch-by-batch
        # view of what has been done.
        if active is not None and active.id == demo_id:
            now = datetime.now(timezone.utc)
            active.batches_history.append(
                BatchHistoryEntry(
                    batch_id=batch_id,
                    batch_index=len(active.batches_history) + 1,
                    dispatched_at=now,
                    invocations=[
                        InvocationHistoryEntry(
                            call_id=itc["id"],
                            name=itc["name"],
                            arguments=self._tool_call_args.get(itc["id"], {}),
                            status="in_progress",
                            result=None,
                        )
                        for itc in internal_tool_calls
                    ],
                )
            )

        self._in_flight_batch = InFlightBatch(
            demo_id=demo_id,
            batch_id=batch_id,
            expected_size=len(internal_tool_calls),
            dispatched_at=datetime.now(timezone.utc),
            tool_names=[itc["name"] for itc in internal_tool_calls],
        )
        self._in_flight_batch.timeout_task = self.create_task(
            self._batch_timeout_handler(batch_id=batch_id),
            f"in-app-batch-timeout-{batch_id[:8]}",
        )

        # Atomic send — one RTVI server message carrying the whole
        # batch. The widget runs each tool sequentially via its queue
        # and reports back per-call ``tool_result`` messages tagged
        # with the same batch_id.
        await self._send_tool_call_batch_to_widget(
            batch_id=batch_id, internal_tool_calls=internal_tool_calls,
        )

        await self._tool_dispatcher.dispatch_batch(
            demo_id=demo_id, batch_id=batch_id, tool_calls=internal_tool_calls,
        )

    async def _batch_timeout_handler(self, *, batch_id: str) -> None:
        """If the batch ``batch_id`` hasn't fully resolved within
        ``BATCH_TIMEOUT_SECONDS``, cancel its still-running tool tasks
        and force-complete each unresolved call with an error result.
        Result: a normal TOOL_BATCH_COMPLETED frame fires (carrying the
        same ``batch_id``); the LLM sees the partial outcomes (some
        success, some "did not finish in time") and decides what to do."""
        try:
            await asyncio.sleep(self._batch_timeout_seconds)
        except asyncio.CancelledError:
            return
        if self._ended:
            return
        if (
            self._in_flight_batch is None
            or self._in_flight_batch.batch_id != batch_id
        ):
            return  # batch already resolved or replaced

        demo_id = self._in_flight_batch.demo_id
        bucket = self._batch_pending.get(batch_id) or {}
        expected = self._batch_expected_size.get(batch_id, 0)
        if expected and len(bucket) >= expected:
            return  # raced — already fully resolved

        logger.warning(
            f"[in-app] batch {batch_id[:8]} (demo {demo_id[:8]}) hit "
            f"{self._batch_timeout_seconds:.0f}s timeout: {len(bucket)}/{expected} "
            "resolved; force-completing"
        )
        # Cancel the dispatcher's still-running tasks for this demo.
        # (cancel_demonstration is demo-scoped — that's fine, only one
        # batch per demo can be in flight at a time so this only kills
        # the right one.)
        try:
            await self._tool_dispatcher.cancel_demonstration(demo_id)
        except Exception as e:
            logger.warning(f"[in-app] cancel_demonstration on timeout: {e}")

        # Fabricate error outcomes for each unresolved call. We can
        # find them by walking the per-call name registry: any call_id
        # we recorded at dispatch but never wrote into the bucket.
        unresolved_call_ids = [
            call_id
            for call_id in list(self._tool_call_names.keys())
            if call_id not in bucket
        ]
        timeout_msg = (
            f"tool did not finish within the "
            f"{self._batch_timeout_seconds:.0f}-second batch timeout; "
            "the batch was force-completed before this call returned"
        )
        for call_id in unresolved_call_ids:
            if (
                self._active_demonstration is not None
                and self._active_demonstration.id == demo_id
            ):
                self._update_invocation_in_batch_history(
                    batch_id=batch_id, call_id=call_id,
                    status="timeout", result=timeout_msg,
                )
            bucket[call_id] = {"error": timeout_msg}
            self._tool_call_names.pop(call_id, None)
            self._tool_call_args.pop(call_id, None)

        # Enqueue the synthetic TOOL_BATCH_COMPLETED so the agent
        # processes the (now complete-with-errors) batch normally. The
        # frame carries the batch_id so the staleness guard in
        # :meth:`_handle_agent_turn` can verify it's still current.
        self._batch_pending.pop(batch_id, None)
        self._batch_expected_size.pop(batch_id, None)
        self._enqueue(
            priority=3,
            frame=InAppMessageFrame(
                message="",
                message_type=MessageType.TOOL_BATCH_COMPLETED,
                put_back_when_interrupted=True,
                demonstration_id=demo_id,
                data={
                    "batch_id": batch_id,
                    "size": expected or len(bucket),
                    "batch_timeout": True,
                    "unresolved_count": len(unresolved_call_ids),
                },
            ),
        )

    async def _resolve_pending_batch(
        self,
        *,
        resolution: Literal["accept", "replace", "keep_waiting"],
        speech: str,
        replacement_invocations: list[InAppToolInvocation],
    ) -> None:
        """Consume the pending-confirmation state per the LLM's
        resolution choice. Speech (if any) was already streamed during
        ``_llm_round_streaming``; here we only do bookkeeping.

        No plain "decline" path: declining without a replacement OR
        ending the demonstration would leave the demo stuck (no
        in-flight batch to drive the next inference). The schema
        enforces this — when the visitor declines, the LLM must use
        ``replace`` (with a different batch) or
        ``demonstration_action='end_current'``.
        """
        pending = self._pending_confirmation_batch
        if pending is None:
            # Schema should make this impossible — log and bail.
            logger.warning(
                "[in-app] pending_batch_resolution received with no "
                "pending batch; ignoring"
            )
            return

        if resolution == "accept":
            self._pending_confirmation_batch = None
            if speech:
                self._history.append_assistant_text(speech)
            await self._dispatch_batch_now(
                internal_tool_calls=pending.internal_tool_calls,
                demo_id=pending.demo_id,
                batch_id=pending.batch_id,
            )
            return

        if resolution == "replace":
            if not replacement_invocations:
                # Empty replacement = no actual replacement. Falling
                # through to keep_waiting (batch stays parked, just
                # speech this turn) is safer than dropping the pending
                # batch with no replacement, which would leave the
                # demonstration stuck.
                logger.warning(
                    "[in-app] pending_batch_resolution=replace with "
                    "empty tool_invocations; falling through to keep_waiting"
                )
                if speech:
                    self._history.append_assistant_text(speech)
                return
            self._pending_confirmation_batch = None
            await self._dispatch_or_park_for_confirmation(
                output_tool_invocations=replacement_invocations, speech=speech,
            )
            return

        # keep_waiting: just speech, no state change.
        if speech:
            self._history.append_assistant_text(speech)

    async def _handle_inference_failure(
        self,
        *,
        wake_mode: str,
        wake_frame: Optional[InAppMessageFrame],
    ) -> None:
        """LLM inference threw / timed out. For TOOL_BATCH_COMPLETED
        wakes we re-enqueue the same frame so the next cycle retries
        the inference (preserving the in-flight state) — up to
        ``self._inference_retry_limit``. On the (cap+1)-th failure we apologise
        terminally and force-end the demo to break the loop. For other
        wakes we just speak the generic apology and don't re-enqueue.
        """
        if (
            wake_mode == "tool_batch_completed"
            and self._in_flight_batch is not None
            and wake_frame is not None
        ):
            self._in_flight_batch.inference_attempts += 1
            attempts = self._in_flight_batch.inference_attempts
            if attempts >= self._inference_retry_limit:
                logger.error(
                    f"[in-app] tool-batch-completed inference failed "
                    f"{attempts}/{self._inference_retry_limit} times; "
                    "force-ending demonstration"
                )
                await self._force_end_demonstration_with_apology(
                    CannedKey.INFERENCE_RETRY_EXHAUSTED
                )
                return
            logger.warning(
                f"[in-app] tool-batch-completed inference failed "
                f"(attempt {attempts}/{self._inference_retry_limit}); re-enqueueing"
            )
            self._speak_canned(CannedKey.LLM_GENERIC_ERROR)
            # Re-enqueue the same TOOL_BATCH_COMPLETED frame at the same
            # priority so the next cycle retries. The canned apology
            # (priority 0) will run BEFORE the retry (priority 3),
            # which matches what the visitor experiences: hear the
            # apology, then the agent re-tries.
            self._enqueue(priority=3, frame=wake_frame)
            return

        # Non tool-batch wake: just apologise; the user will re-state.
        self._speak_canned(CannedKey.LLM_GENERIC_ERROR)

    # ------------------------------------------------------------------
    # Per-turn agent prompt (single Jinja template, end-to-end)
    # ------------------------------------------------------------------

    def _build_state_context_message(
        self, *, wake_mode: Optional[str] = None
    ) -> str:
        """Compat shim — alias to :meth:`_render_agent_turn_prompt`.

        Older test code calls ``_build_state_context_message`` and
        expects to inspect the per-round prompt. The current production
        path is ``_render_agent_turn_prompt``, which renders the SINGLE
        Jinja template containing everything (persona, scope, software,
        tools, state, history). Both names return the same string.

        Default wake is ``user_voice`` rather than ``system`` because
        system wakes are now the speech-only short-circuit (no demo
        state, no batch history, no action enum render). Tests that
        want to inspect those rich sections rely on the user-wake
        default.
        """
        return self._render_agent_turn_prompt(
            wake_mode=wake_mode or "user_voice",
            batch_state=self._compute_batch_state(),
        )

    def _render_agent_turn_prompt(
        self,
        *,
        wake_mode: Literal[
            "user_voice", "user_text", "tool_batch_completed",
            "kickoff", "screenshot_result",
        ],
        batch_state: Literal["idle", "in_flight", "pending_confirmation"],
        screenshot_context: Optional[dict] = None,
    ) -> str:
        """Render the SINGLE end-to-end system prompt for this turn.

        One Jinja template (:data:`IN_APP_AGENT_TURN_TEMPLATE`)
        carries everything the LLM sees: persona, scope, software docs,
        tools, current demonstration state, batch history, allowed
        actions for this wake, all conditional rule sections, and the
        full conversation history rendered inline. One place to read,
        one place to modify.
        """
        active = self._active_demonstration
        results_just_landed = (
            wake_mode == "tool_batch_completed" and batch_state == "in_flight"
        )
        idle_stage_two_armed = self._is_idle_stage_two_armed()
        # Original wake mode flows through ``screenshot_context`` for
        # screenshot_result turns — guide mode uses it to decide
        # whether to expose the voice gates (relevance + completeness)
        # on this round. Action mode ignores the field. Default-None
        # for non-screenshot wakes is fine; the schema builder only
        # consults it when wake_mode == "screenshot_result".
        original_wake_mode = (
            screenshot_context.get("original_wake_mode")
            if screenshot_context
            else None
        )
        schema = build_in_app_schema(
            wake_mode=wake_mode,
            batch_state=batch_state,
            tools=self._config.tools,
            idle_stage_two_armed=idle_stage_two_armed,
            mode=self._config.mode,
            original_wake_mode=original_wake_mode,
        )
        tool_invocations_in_schema = "tool_invocations" in schema["properties"]
        any_confirmable_tools = any(
            t.requires_confirmation for t in self._config.tools
        )
        mid_demo_user_guidance_active = (
            active is not None
            and wake_mode in ("user_voice", "user_text")
            and batch_state != "pending_confirmation"
        )
        budget_warning = (
            active is not None
            and active.tool_batches_dispatched
            >= self._max_tool_batches_per_demonstration - 1
        )

        active_view = (
            self._serialize_demo_for_template(
                active,
                batch_state=batch_state,
                results_just_landed=results_just_landed,
            )
            if active is not None
            else None
        )
        pending_view = (
            self._serialize_pending_for_template()
            if batch_state == "pending_confirmation"
            else None
        )
        summary, history_messages = self._history.get_log_for_template()

        # Split history into prior context vs the trigger message. On
        # user wakes the latest entry in the log IS the utterance that
        # caused this round, so we flag it separately. On non-user
        # wakes (tool_batch_completed / system) the trigger isn't in
        # the conversation log — it's the batch result or system
        # event — so the entire log is prior context.
        if wake_mode in ("user_voice", "user_text") and history_messages:
            prior_messages = history_messages[:-1]
            trigger_message = history_messages[-1]
        else:
            prior_messages = history_messages
            trigger_message = None

        wake_reason_human = self._wake_reason_human_for(wake_mode)

        ctx = {
            **self._config.session_static_template_context(
                output_language=self._output_language,
            ),
            "wake_mode": wake_mode,
            "wake_reason_human": wake_reason_human,
            "active_demonstration": active_view,
            "pending_confirmation_batch": pending_view,
            "tool_invocations_in_schema": tool_invocations_in_schema,
            "any_confirmable_tools": any_confirmable_tools,
            "mid_demo_user_guidance_active": mid_demo_user_guidance_active,
            "idle_stage_two_armed": idle_stage_two_armed,
            "budget_warning": budget_warning,
            "max_tool_batches_per_demonstration": self._max_tool_batches_per_demonstration,
            "conversation_summary": summary,
            "prior_messages": prior_messages,
            "trigger_message": trigger_message,
            # Populated only on screenshot_result wakes — carries the
            # original visitor utterance + the agent's brief
            # "I'll take a look" acknowledgment so the LLM, now
            # holding the image, has the textual context the request
            # was made in. The Jinja template references
            # screenshot_context.user_utterance and
            # screenshot_context.agent_acknowledgment under the
            # screenshot_result wake branch.
            "screenshot_context": screenshot_context,
        }
        # Mode picks the template — guide mode is a fundamentally
        # different operating envelope (no tools, no demos, screen-on-
        # every-turn) and shares too few sections with action mode to
        # be worth conditional rendering. Two clean templates beat one
        # over-conditional one. The shared context dict carries every
        # variable both templates reference; the templates ignore the
        # ones they don't use.
        if self._config.mode == "guide":
            return IN_APP_AGENT_GUIDE_TURN_TEMPLATE.render(**ctx)
        return IN_APP_AGENT_TURN_TEMPLATE.render(**ctx)

    @staticmethod
    def _wake_reason_human_for(wake_mode: str) -> str:
        """Short human-language label for the current wake. The detailed
        per-wake guidance lives in the template's ALLOWED ACTIONS / etc.
        sections — this is just the headline."""
        if wake_mode == "user_voice":
            return "The visitor spoke (voice transcript). Respond to them."
        if wake_mode == "user_text":
            return "The visitor sent typed text. Respond to them."
        if wake_mode == "tool_batch_completed":
            return (
                "The latest tool batch under the active demonstration "
                "just resolved (results in the batch history below)."
            )
        return (
            "System wake — kickoff (visitor opened the widget) or an "
            "incomplete-prompt nudge."
        )

    def _serialize_demo_for_template(
        self,
        active: "ActiveDemonstration",
        *,
        batch_state: Literal["idle", "in_flight", "pending_confirmation"],
        results_just_landed: bool,
    ) -> dict:
        """Project the active demo + its batch history into the shape
        the Jinja template iterates over. Keeps template logic simple
        and pure-Jinja (no method calls or attribute hops on Python
        objects)."""
        current_batch_id = (
            self._in_flight_batch.batch_id
            if self._in_flight_batch is not None
            else (
                self._pending_confirmation_batch.batch_id
                if self._pending_confirmation_batch is not None
                else None
            )
        )
        batches_view = []
        for batch in active.batches_history:
            is_current = batch.batch_id == current_batch_id
            invocations_view = []
            for inv in batch.invocations:
                if inv.status == "in_progress":
                    result_repr = "in_progress (no result yet)"
                elif inv.result is None:
                    result_repr = inv.status
                else:
                    result_repr = f"{inv.status}: {inv.result!s:.180s}"
                invocations_view.append(
                    {
                        "name": inv.name,
                        "arguments_json": json.dumps(inv.arguments),
                        "result_repr": result_repr,
                    }
                )
            batches_view.append(
                {
                    "batch_index": batch.batch_index,
                    "dispatched_at_iso": batch.dispatched_at.isoformat(),
                    "is_just_resolved": is_current and results_just_landed,
                    "is_in_flight": (
                        is_current
                        and not results_just_landed
                        and batch_state == "in_flight"
                    ),
                    "is_pending_confirmation": (
                        is_current and batch_state == "pending_confirmation"
                    ),
                    "invocations": invocations_view,
                }
            )
        return {
            "name": active.name,
            "started_at_iso": active.started_at.isoformat(),
            "tool_batches_dispatched": active.tool_batches_dispatched,
            "batches_history": batches_view,
        }

    def _serialize_pending_for_template(self) -> dict:
        """Project the parked-pending batch into a Jinja-friendly view."""
        pending = self._pending_confirmation_batch
        assert pending is not None
        invocations_view = []
        for tc in pending.openai_tool_calls:
            name = tc["function"]["name"]
            invocations_view.append(
                {
                    "name": name,
                    "arguments_json": tc["function"]["arguments"],
                    "is_confirmable": name in pending.confirmable_tool_names,
                }
            )
        return {
            "size": len(pending.openai_tool_calls),
            "confirmable_names_csv": ", ".join(pending.confirmable_tool_names),
            "proposed_invocations": invocations_view,
        }

    # ==================================================================
    # Tool dispatch — called by the dispatcher
    # ==================================================================

    async def _send_tool_call_batch_to_widget(
        self,
        *,
        batch_id: str,
        internal_tool_calls: list[dict],
    ) -> None:
        """Atomic per-batch send. The widget receives a single
        ``tool_call_batch`` server message carrying every tool in the
        batch, then runs them sequentially via its own queue and
        reports back per-tool ``tool_result`` messages tagged with the
        same batch_id.

        Sending as one unit avoids the half-sent-batch failure mode:
        if the bot crashes between two per-tool sends, the widget
        would otherwise have run tool 1 and never been told about
        tool 2.
        """
        logger.info(
            f"[in-app] → send_server_message type=tool_call_batch "
            f"batch_id={batch_id[:8]} ntools={len(internal_tool_calls)}"
        )
        await self._rtvi.send_server_message(
            {
                "type": "tool_call_batch",
                "batch_id": batch_id,
                "tool_calls": [
                    {
                        "call_id": itc["id"],
                        "name": itc["name"],
                        "args": (
                            json.loads(itc["arguments"])
                            if isinstance(itc.get("arguments"), str)
                            else (itc.get("arguments") or {})
                        ),
                    }
                    for itc in internal_tool_calls
                ],
            }
        )

    async def _on_tool_outcome(
        self,
        demo_id: str,
        batch_id: str,
        call_id: str,
        name: str,
        args: dict,
        outcome: str,
        payload: object,
    ) -> None:
        """Append the per-tool result to history and, when the batch
        completes, enqueue TOOL_BATCH_COMPLETED so the agent can
        announce progress and decide what's next.

        Stale-result discipline (four guards, top-down):

        1. ``demo_id`` must match the active demonstration. A late
           result from an interrupted demo is by definition stale.
        2. ``batch_id`` must match the in-flight batch's ``batch_id``.
           Even within the same demo, results from a previous batch
           that was timed-out / replaced / consumed don't apply to
           the currently-running batch.
        3. ``call_id`` must still be in ``self._tool_call_names`` —
           that registry is populated at dispatch and removed by
           *exactly one* consumer (this method on success, or
           :meth:`_batch_timeout_handler` when the 60s cap fires and
           we force-finalize unresolved calls). If it's gone, the
           batch was already finalized; drop the late result.
        4. There must be an in-flight batch (`_in_flight_batch is not
           None`). If we're back to idle / pending, we're past the
           batch this result belongs to.
        """
        if demo_id != self._current_demonstration_id:
            logger.info(
                f"[in-app] dropping outcome for stale demo {demo_id} "
                f"(current={self._current_demonstration_id})"
            )
            return

        if (
            self._in_flight_batch is None
            or self._in_flight_batch.batch_id != batch_id
        ):
            current_bid = (
                self._in_flight_batch.batch_id
                if self._in_flight_batch is not None
                else None
            )
            logger.info(
                f"[in-app] dropping outcome for call_id={call_id} "
                f"(batch={batch_id[:8] if batch_id else '?'}) — "
                f"no matching in-flight batch "
                f"(current={current_bid[:8] if current_bid else 'none'}; "
                "batch was cancelled, replaced, or already consumed)"
            )
            # Defensive cleanup so a late callback doesn't leave a
            # zombie name/args entry behind.
            self._tool_call_names.pop(call_id, None)
            self._tool_call_args.pop(call_id, None)
            return

        if call_id not in self._tool_call_names:
            # Either this call_id was already force-finalized during a
            # batch-level timeout, or the dispatcher fired the callback
            # twice. Either way the batch has already been recorded;
            # accepting it now would corrupt the per-tool history with
            # a duplicate / overriding entry.
            logger.info(
                f"[in-app] dropping stale outcome for call_id={call_id} "
                f"(name={name}) — batch already finalized"
            )
            return

        if outcome == ToolDispatchOutcome.SUCCESS:
            status = "success"
            result_obj: Any = payload
        elif outcome == ToolDispatchOutcome.FAILED:
            status = "error"
            result_obj = str(payload)
        else:
            return  # CANCELLED — dispatcher skips the callback for these

        # Analytics: log the tool call to the per-session audit log
        # AFTER all staleness guards pass. We capture both successes
        # and errors so the dashboard can show the full picture of
        # what the agent attempted.
        self._record_tool_call_for_analytics(
            name=name,
            args=args,
            result={"status": status, "value": result_obj},
        )

        # Update the active demo's structured batch history so the
        # next inference's state-context block shows the right batch
        # → invocation → status/result tree.
        if (
            self._active_demonstration is not None
            and self._active_demonstration.id == demo_id
        ):
            self._update_invocation_in_batch_history(
                batch_id=batch_id, call_id=call_id,
                status=status, result=result_obj,
            )
        # Per-call bookkeeping used at dispatch time is now consumed.
        self._tool_call_names.pop(call_id, None)
        self._tool_call_args.pop(call_id, None)

        bucket = self._batch_pending.setdefault(batch_id, {})
        bucket[call_id] = (
            {"result": result_obj} if status == "success" else {"error": result_obj}
        )
        expected = self._batch_expected_size.get(batch_id, 0)
        if expected and len(bucket) >= expected:
            self._batch_pending.pop(batch_id, None)
            self._batch_expected_size.pop(batch_id, None)
            self._enqueue(
                priority=3,
                frame=InAppMessageFrame(
                    message="",
                    message_type=MessageType.TOOL_BATCH_COMPLETED,
                    put_back_when_interrupted=True,
                    demonstration_id=demo_id,
                    data={"batch_id": batch_id, "size": expected},
                ),
            )

    # ==================================================================
    # Wake-reason builder (legacy — retained only in case a future
    # caller needs the prose form; the in-flight prompt-render path no
    # longer touches this. Safe to delete once nothing references it.)
    # ==================================================================

    def _wake_reason_for(self, frame: InAppMessageFrame) -> str:
        if frame.message_type == MessageType.KICKOFF:
            return (
                "The visitor just opened the widget. Greet them briefly, ask how you "
                "can help, and use a tool only if they explicitly ask for something."
            )
        if frame.message_type == MessageType.TOOL_BATCH_COMPLETED:
            ifb = self._in_flight_batch
            tool_list = (
                ", ".join(ifb.tool_names)
                if ifb is not None and ifb.tool_names
                else "the previous batch"
            )
            return (
                f"Tool calls just resolved: {tool_list}. Speak a short "
                "progress update so the visitor knows what happened, "
                "then decide whether more tools are needed or the "
                "request is fully handled."
            )
        return "(user spoke)"

    # ==================================================================
    # Interruption
    # ==================================================================

    async def _handle_interruption(self) -> None:
        """User cut in. Cancel the in-flight bot-turn close-task,
        drain ephemeral queue items, and reset state so the next
        user message lands cleanly.

        We do NOT cancel pending tool tasks here — the LLM at the next
        round will decide whether to keep using their results or
        redirect, and the redirect path goes through
        :meth:`_start_new_demonstration` which cancels them then.
        """
        logger.info("[in-app] handling interruption")

        # Note: interruption alone does NOT cancel the idle timer.
        # Random noise / a side-conversation fragment can cause an
        # interruption (any voice activity does), and the visitor
        # hasn't actually said anything to us. Only a VALID user
        # turn (text, or voice classified relevant) cancels — that
        # decision is made downstream in _run_one_round and acted on
        # by _after_round_completed_naturally.
        await self._cancel_reply_watchdog()
        await self._cancel_pump_task()

        # Drain queue: keep only put_back items. Ephemeral frames are
        # dropped — they're stale after an interruption.
        items_to_keep: list[RankedEnvelope] = []
        while not self._wake_queue.empty():
            try:
                item = self._wake_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            f = item.frame
            keep = f is not None and f.put_back_when_interrupted
            if keep:
                items_to_keep.append(item)
            self._wake_queue.task_done()
        for item in items_to_keep:
            self._wake_queue.put_nowait(item)

        # Re-enqueue the in-flight frame if it asked to be put back.
        if (
            not self._ended
            and self._frame_in_flight is not None
            and self._frame_in_flight.frame is not None
            and self._frame_in_flight.frame.put_back_when_interrupted
        ):
            self._wake_queue.put_nowait(copy.deepcopy(self._frame_in_flight))
        self._frame_in_flight = None

        self._processing_blocked = False
        self._processing_event.clear()

        await self._emit_to_widget({"type": "assistant_interrupted"})

    # ==================================================================
    # Aggregator turn handlers
    # ==================================================================

    def _wire_aggregator_turn_handlers(self) -> None:
        """Bind handlers onto the aggregator instances so the bot/user
        turn lifecycle drives our state machine.
        ``event_handler("on_X")(callback)`` is the public registration
        API on the universal LLM aggregators (see
        ``llm_response_universal.py`` events ``on_user_turn_started``,
        ``on_user_turn_stopped``, ``on_assistant_turn_started``,
        ``on_assistant_turn_stopped``)."""
        self._user_aggregator.event_handler("on_user_turn_started")(
            self._user_turn_started_handler
        )
        self._user_aggregator.event_handler("on_user_turn_stopped")(
            self._user_turn_stopped_handler
        )
        self._assistant_aggregator.event_handler("on_assistant_turn_started")(
            self._assistant_turn_started_handler
        )
        self._assistant_aggregator.event_handler("on_assistant_turn_stopped")(
            self._assistant_turn_stopped_handler
        )

    async def _user_turn_started_handler(self, *_args, **_kwargs) -> None:
        # NOTE: we deliberately do NOT cancel the incomplete-prompt
        # timeout here. Per the design contract, the timeout is only
        # cancelled on (a) starting a new one, (b) interruption,
        # (c) processor stop. If the user starts speaking again, their
        # next utterance is classified by the LLM in the main inference;
        # if it's also incomplete, _start_incomplete_timeout cancels the
        # prior one before starting a fresh one.
        logger.info("[in-app] user turn started")
        await self._emit_to_widget({"type": "user_turn_started"})

    async def _user_turn_stopped_handler(self, *_args, **_kwargs) -> None:
        logger.info("[in-app] user turn ended")
        await self._emit_to_widget({"type": "user_turn_ended"})

    async def _assistant_turn_started_handler(self, *_args, **_kwargs) -> None:
        logger.info("[in-app] assistant turn started")
        await self._emit_to_widget({"type": "assistant_turn_started"})
        await self._fire_reply_watchdog()
        self._frame_in_flight = None

    async def _assistant_turn_stopped_handler(self, *_args, **_kwargs) -> None:
        logger.info("[in-app] assistant turn ended")
        await self._emit_to_widget({"type": "assistant_turn_ended"})
        await self._try_finalize_turn()

    async def _try_finalize_turn(self) -> None:
        if self._finalizing_turn:
            return
        self._finalizing_turn = True
        try:
            if self._pump_task is not None:
                self._resume_processing()
        finally:
            self._finalizing_turn = False

    # ==================================================================
    # Background tasks
    # ==================================================================

    async def _session_cap_warning_runner(self) -> None:
        """Sleep until SESSION_CAP_WARNING_AFTER_SECONDS into the
        session, then speak a canned heads-up that the session will
        close in about 10 minutes. Runs once per session; fires
        regardless of idle/active state. The actual disconnect at the
        90-minute mark is handled widget-side (the launcher's own
        timer at SESSION_HARD_CAP_MS) plus the server's ``wait_for``
        at MAXIMUM_SESSION_DURATION_MINUTES as a backup — this
        watchdog only handles the courtesy warning."""
        try:
            await asyncio.sleep(SESSION_CAP_WARNING_AFTER_SECONDS)
        except asyncio.CancelledError:
            raise
        if self._ended:
            return
        logger.info(
            f"[in-app] session cap warning at "
            f"{int(SESSION_CAP_WARNING_AFTER_SECONDS)}s — speaking canned "
            "10-minute heads-up"
        )
        self._speak_canned(CannedKey.SESSION_CAP_WARNING, put_back_when_interrupted=True)

    async def _heartbeat_timeout_monitor(self) -> None:
        """If we go HEARTBEAT_TIMEOUT_SECONDS without a widget heartbeat,
        push CancelTaskFrame upstream so the bot terminates cleanly."""
        try:
            while not self._ended:
                await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL_SECONDS)
                if self._ended:
                    return
                if not self._session_started or self._last_heartbeat_time is None:
                    continue
                elapsed = time.time() - self._last_heartbeat_time
                if elapsed > self._heartbeat_timeout_seconds:
                    logger.warning(
                        f"[in-app] heartbeat timeout ({elapsed:.1f}s) — terminating"
                    )
                    await self.push_frame(CancelTaskFrame(), FrameDirection.UPSTREAM)
                    return
        except asyncio.CancelledError:
            raise

    async def _run_reply_watchdog(self) -> None:
        """Recovery: if the processor accepts work but doesn't push a
        response within :data:`REPLY_WATCHDOG_SECONDS`, fire an
        interrupt + soft error message."""
        try:
            assert self._reply_watchdog_signal is not None
            self._reply_watchdog_signal.clear()
            try:
                await asyncio.wait_for(
                    self._reply_watchdog_signal.wait(),
                    timeout=self._reply_watchdog_seconds,
                )
            except asyncio.TimeoutError:
                if self._ended:
                    return
                logger.warning("[in-app] response timeout — recovery")
                await self.push_interruption_task_frame_and_wait()
                await self._handle_interruption()
                await self.push_frame(InterruptionFrame(), direction=FrameDirection.DOWNSTREAM)
                # Enqueue the canned multilingual apology. We don't
                # enqueue a SYSTEM frame for the LLM to author the
                # apology — the LLM might BE the failing component;
                # the whole point of this recovery is to get a
                # guaranteed response to the visitor. The pump task
                # was just cancelled by _handle_interruption, so
                # _speak_canned's _create_pump_task call brings
                # it back up to process the canned-speech frame.
                self._speak_canned(CannedKey.RESPONSE_TIMEOUT)
                self._create_pump_task()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[in-app] response-timeout processor errored: {e}")

    def _create_reply_watchdog(self) -> None:
        if self._reply_watchdog_task is not None:
            return
        self._reply_watchdog_signal = asyncio.Event()
        self._reply_watchdog_task = self.create_task(
            self._run_reply_watchdog(),
            "in-app-check-response",
        )

    # ==================================================================
    # Idle-session timeout (visitor went quiet)
    # ==================================================================
    #
    # Cancel-and-rearm design (NOT a polling pause).
    #
    # WHEN the timer is ARMED:
    #   At the end of an agent turn that finished NATURALLY — meaning
    #   ``_run_one_round`` returned without an interruption mid-stream —
    #   AND the resulting batch state is one of:
    #     * ``idle``                  (no demo, or demo with nothing
    #                                  in flight, between asks)
    #     * ``pending_confirmation``  (confirmable batch parked,
    #                                  waiting on the visitor's yes/no)
    #   If the round dispatched tools (state ``in_flight``) we do NOT
    #   arm — the next natural turn-end after the batch resolves rearms.
    #
    # WHEN the timer is CANCELLED:
    #   * Any new round is about to run (rearm replaces the old one).
    #   * The visitor interrupts (handled in ``_handle_interruption``).
    #   * The bot is stopping.
    #
    # STAGES:
    #   1. Warning task sleeps ``_idle_warning_after_seconds``. On wake,
    #      enqueues a SYSTEM wake telling the agent to check in and warn
    #      about closure, then spawns the end task.
    #   2. End task sleeps ``_idle_end_after_warning_seconds``. On wake,
    #      force-ends with a canned goodbye and tears the bot down.
    #   Either task can be cancelled by ``_cancel_idle_timer``.

    def _is_idle_stage_two_armed(self) -> bool:
        """True iff the warning fired and the 60-second grace task is
        currently running. Read by ``build_in_app_schema`` to
        expose the ``idle_warning_resolution`` field this round."""
        t = self._idle_end_task
        return t is not None and not t.done()

    def _is_any_idle_timer_armed(self) -> bool:
        """True iff stage 1 OR stage 2 task is currently running."""
        w = self._idle_warning_task
        if w is not None and not w.done():
            return True
        return self._is_idle_stage_two_armed()

    async def _arm_idle_timer_if_appropriate(self) -> None:
        """Single-purpose: arm stage 1 ONLY when all five conditions
        hold simultaneously. Never cancels, never touches a running
        timer — pure arm-if-empty.

        Conditions:
          1. Bot is alive and the session has started.
          2. No idle timer (stage 1 or stage 2) is currently running.
          3. Batch state is ``idle`` or ``pending_confirmation``
             (i.e. NOT ``in_flight`` — agent is between asks or
             waiting on the visitor's confirmation, NOT mid-tool-work).
          4. No screenshot fetch is in flight. Same reasoning as the
             batch-state gate: a screenshot fetch is the agent
             actively working on the visitor's behalf — the idle
             timer's "still there?" check-in would be wrong here, and
             the SCREENSHOT_RESULT round that lands shortly will
             re-arm via the post-round dispatcher anyway. Especially
             important in guide mode where every user wake spawns a
             fetch and the LLM only runs at screenshot_result.
          5. No pending message in the priority queue. Anything sitting
             on the queue (SCREENSHOT_RESULT bytes that just landed,
             TOOL_BATCH_COMPLETED waiting to inference, a queued
             user message, etc.) means the agent is about to produce
             a response. Firing "still there?" between the enqueue
             and the actual round would race the agent's own answer
             — the visitor would hear the check-in just before / on
             top of the response. The dispatcher arms again after the
             pending frame is processed anyway, so deferring here
             costs nothing.
        """
        if self._ended or not self._session_started:
            return
        if self._is_any_idle_timer_armed():
            return
        if self._compute_batch_state() == "in_flight":
            return
        if self._screenshot_service.pending_count > 0:
            return
        if not self._wake_queue.empty():
            return
        self._idle_warning_task = self.create_task(
            self._idle_warning_runner(),
            "in-app-idle-warning",
        )

    async def _cancel_idle_timer(self) -> None:
        """Cancel both the warning task and (if armed) the end task.
        Mirrors the same shape as ``_cancel_reply_watchdog``
        — uses the inherited ``FrameProcessor.cancel_task`` rather
        than ``Task.cancel()`` so the cancellation is awaited and
        registered the same way every other lifecycle task is."""
        warning = self._idle_warning_task
        self._idle_warning_task = None
        end = self._idle_end_task
        self._idle_end_task = None
        if warning is not None and not warning.done():
            await self.cancel_task(warning)
        if end is not None and not end.done():
            await self.cancel_task(end)

    async def _idle_warning_runner(self) -> None:
        """Sleep until the warning threshold, then speak the canned
        "are you still there?" check-in and arm the end task that
        handles the grace-period auto-shutdown.

        We use canned speech (not an LLM round on a SYSTEM wake) here
        because the wording is fixed: "still there? closing in a
        minute." The text is deterministic, multilingual, and the
        check-in fires hundreds of times per day across sessions —
        running an inference round per fire is wasted compute.
        ``_handle_canned_speech`` already pushes the same
        Start/Text/End frame triplet a real round would, appends to
        the conversation history, and resets the response-timeout
        watchdog, so the visitor and the bot's working memory both
        see the warning landing exactly like an agent-spoken turn."""
        try:
            await asyncio.sleep(self._idle_warning_after_seconds)
        except asyncio.CancelledError:
            raise
        if self._ended:
            return
        # Belt-and-suspenders: re-check the gating condition at fire
        # time. Something might have moved the state to in_flight
        # between arming and wake (rare race, but the check is cheap).
        if self._compute_batch_state() == "in_flight":
            return

        warning = int(self._idle_warning_after_seconds)
        logger.info(
            f"[in-app] visitor idle for {warning}s — speaking "
            "canned idle-warning check-in"
        )
        self._speak_canned(CannedKey.IDLE_WARNING)

        # Arm the end task. It will be cancelled by the next natural
        # round if the visitor replies (because the round-end handler
        # calls _cancel_idle_timer before re-arming).
        self._idle_end_task = self.create_task(
            self._idle_end_runner(),
            "in-app-idle-end",
        )

    async def _idle_end_runner(self) -> None:
        """Sleep through the grace period; if not cancelled, force-end
        the session with the canned goodbye + push CancelTaskFrame
        upstream so the widget sees a clean disconnect."""
        try:
            await asyncio.sleep(self._idle_end_after_warning_seconds)
        except asyncio.CancelledError:
            raise
        if self._ended:
            return
        logger.info(
            "[in-app] idle grace period elapsed — force-ending "
            "the session with goodbye"
        )
        # Same reason as the end_session handler in _run_one_round:
        # set _ended now so any racing arm-helper call in the dying-
        # session window short-circuits.
        self._ended = True
        try:
            await self._force_end_demonstration_with_apology(
                CannedKey.SESSION_IDLE_GOODBYE
            )
        except Exception as e:
            logger.warning(
                f"[in-app] idle-end goodbye apology failed: {e}"
            )
        # Tell the widget we're closing BEFORE we tear the bot down.
        # Gives it a moment to render the goodbye and disconnect on
        # its own terms instead of seeing the WebRTC channel
        # vanishing first.
        await self._signal_session_ending(reason="idle_grace_elapsed")
        try:
            await self.push_frame(
                CancelTaskFrame(), FrameDirection.UPSTREAM
            )
        except Exception as e:
            logger.warning(
                f"[in-app] idle-end CancelTaskFrame push failed: {e}"
            )

    async def _cancel_reply_watchdog(self) -> None:
        task = self._reply_watchdog_task
        self._reply_watchdog_task = None
        if task is not None:
            await self.cancel_task(task)
        if self._reply_watchdog_signal is not None:
            self._reply_watchdog_signal.set()
            self._reply_watchdog_signal = None

    async def _fire_reply_watchdog(self) -> None:
        if self._reply_watchdog_signal is not None:
            self._reply_watchdog_signal.set()

    # ==================================================================
    # Processor task plumbing
    # ==================================================================

    def _create_pump_task(self) -> None:
        if self._pump_task is None:
            self._pump_task = self.create_task(
                self._run_pump_loop(),
                "in-app-msg-pump",
            )

    async def _cancel_pump_task(self) -> None:
        task = self._pump_task
        self._pump_task = None
        if task is None:
            return
        # Set the cancellation flag BEFORE issuing cancel_task so any
        # in-flight LangChain ``astream`` call inside the pump task (or
        # that finishes a microsecond before the cancel signal lands)
        # sees the flag and aborts via the post-loop check in
        # _llm_round_streaming.
        self._cancelling_current_response_generation = True
        try:
            await self.cancel_task(task)
        finally:
            self._cancelling_current_response_generation = False

    async def _run_pump_loop(self) -> None:
        while True:
            try:
                if self._processing_blocked:
                    await self._processing_event.wait()
                    self._processing_blocked = False
                wrapper = await self._wake_queue.get()
                self._frame_in_flight = wrapper
                if wrapper.frame is not None:
                    logger.info(
                        f"[in-app] dequeue priority={wrapper.priority} "
                        f"type={wrapper.frame.message_type.name}"
                    )
                    await self._process_message(wrapper.frame)
                    logger.info(
                        f"[in-app] processed "
                        f"type={wrapper.frame.message_type.name}"
                    )
                self._wake_queue.task_done()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.exception(f"[in-app] processor error: {e}")
                try:
                    self._wake_queue.task_done()
                except ValueError:
                    pass

    def _pause_processing(self) -> None:
        self._processing_blocked = True
        self._processing_event.clear()

    def _resume_processing(self) -> None:
        self._processing_event.set()

    def _enqueue(self, *, priority: int, frame: InAppMessageFrame) -> None:
        preview = (frame.message or "")[:60].replace("\n", " ")
        logger.info(
            f"[in-app] enqueue priority={priority} "
            f"type={frame.message_type.name} "
            f"qsize={self._wake_queue.qsize() + 1} "
            f"msg={preview!r}"
        )
        self._wake_queue.put_nowait(RankedEnvelope(priority=priority, frame=frame))

    # ==================================================================
    # RTVI hookup
    # ==================================================================

    def _setup_rtvi_handlers(self) -> None:
        @self._rtvi.event_handler("on_client_message")
        async def on_client_message(rtvi, msg: RTVIClientMessageFrame):  # noqa: ANN001
            # Log a SUMMARY of the inbound payload instead of the raw
            # dict. ``screenshot_response`` carries a ~50–500 KB
            # base64 image inside ``data.image_b64``; logging the raw
            # ``data`` floods the terminal and makes everything else
            # unreadable. The summary keeps the useful bits (size,
            # request_id) without dumping the bytes.
            if msg.type == "tool_result":
                data = msg.data or {}
                call_id = data.get("call_id")
                if not call_id:
                    return
                self._tool_dispatcher.deliver_widget_result(call_id=call_id, data=data)
            elif msg.type == "send-text-message":
                text = (msg.data or {}).get("text", "").strip()
                if not text:
                    return
                # Typed input is a fresh user turn. It MUST interrupt
                # whatever the bot is doing — same as a voice barge-in,
                # but text bypasses the user-turn-started path so we
                # have to fire the interruption ourselves:
                #   1. push InterruptionFrame downstream so the
                #      aggregator pair resets
                #   2. run _handle_interruption to drain the queue,
                #      cancel the processor task, cancel the incomplete
                #      timeout, etc.
                #   3. then enqueue the new text turn
                await self.push_interruption_task_frame_and_wait()
                await self._handle_interruption()
                await self.push_frame(InterruptionFrame(), direction=FrameDirection.DOWNSTREAM)

                # Voice path emits user_turn_ended via the user-aggregator's
                # on_user_turn_stopped event when STT settles; the widget
                # maps that to its "thinking" pill state. Text bypasses STT
                # entirely, so without this manual emit the widget jumps
                # straight from idle/responding to responding and never
                # shows that the agent is processing the typed turn.
                await self._emit_to_widget({"type": "user_turn_ended"})

                # Idle-timer rearm is handled at the end of the round
                # this enqueue triggers (see _run_one_round dispatcher).
                self._create_reply_watchdog()
                self._enqueue(
                    priority=1,
                    frame=InAppMessageFrame(
                        message=text,
                        message_type=MessageType.TEXT_MESSAGE,
                        put_back_when_interrupted=False,
                    ),
                )
                self._create_pump_task()
            elif msg.type == "heartbeat":
                # Heartbeat fires every ~3s; logging each one drowns
                # the rest of the trace. The watchdog at
                # ``_heartbeat_timeout_monitor`` is what we care about
                # — if heartbeats stop, that loop logs loudly. Successful
                # heartbeats are silent by design.
                if self._session_started:
                    self._last_heartbeat_time = time.time()
                else:
                    logger.warning(
                        "[in-app] heartbeat received before session "
                        "started — dropping"
                    )
            elif msg.type == "screenshot_response":
                # The widget answered a per-inference screenshot
                # request. Route to the broker; the awaiting
                # ``request()`` call in `_llm_round_streaming` resolves.
                # Late / unmatched responses are dropped silently by
                # ScreenshotService.resolve.
                self._screenshot_service.resolve(msg.data or {})
            else:
                logger.debug(f"[in-app] unhandled client message: {msg.type}")

    async def _emit_to_widget(self, data: dict) -> None:
        msg_type = data.get("type") if isinstance(data, dict) else "?"
        try:
            await self._rtvi.send_server_message(data)
        except Exception as e:
            logger.warning(f"[in-app] notify_frontend failed: {e}")

    async def _signal_session_ending(self, *, reason: str) -> None:
        """Tell the widget the bot is closing the session — emitted
        BEFORE the bot tears its pipeline down via CancelTaskFrame.

        The widget should:
          1. Render the goodbye / final message we just spoke.
          2. Wait a brief moment so the visitor can read it.
          3. Disconnect on its own.

        Without this signal the widget sees the WebRTC channel
        vanish first and shows the generic "Connection lost"
        banner — which is wrong UX for a bot that intentionally
        ended the session. ``reason`` distinguishes flavours so the
        widget can tune the copy / delay (visitor_confirmed_end vs
        idle_grace_elapsed)."""
        await self._emit_to_widget(
            {
                "type": "session_ending",
                "reason": reason,
                # Hint to the widget: keep the goodbye visible for a
                # few seconds before closing the panel. Widget is
                # free to override.
                "linger_seconds": 4,
            }
        )

    # ==================================================================
    # Lifecycle
    # ==================================================================

    async def _start(self, frame: StartFrame) -> None:
        self._session_started_at = datetime.now(timezone.utc)
        self._heartbeat_task = self.create_task(
            self._heartbeat_timeout_monitor(),
            "in-app-heartbeat",
        )
        self._session_cap_warning_task = self.create_task(
            self._session_cap_warning_runner(),
            "in-app-session-cap-warning",
        )
        self._create_pump_task()
        logger.info("[in-app] processor started")

    async def _stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        await self._cancel_reply_watchdog()
        await self._cancel_idle_timer()
        heartbeat = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat is not None:
            await self.cancel_task(heartbeat)
        cap_warning = self._session_cap_warning_task
        self._session_cap_warning_task = None
        if cap_warning is not None and not cap_warning.done():
            await self.cancel_task(cap_warning)
        await self._cancel_pump_task()
        await self._clear_in_flight_batch()
        await self._tool_dispatcher.shutdown()
        # Drop any in-flight screenshot awaiters; without this they
        # would block past pipeline tear-down.
        self._screenshot_service.cancel_all()
        await self._history.shutdown()
        # NOTE: if you wire up session analytics, do it from bot.py's
        # outer ``finally`` block (after ``runner.run(task)`` completes)
        # — not from this method. POSTing during tear-down is fragile
        # (aiohttp session may already be closing, processor may be
        # mid-cancel). OSS Voqi ships no analytics POST by default; the
        # snapshot is available via :meth:`get_session_data`.

    def _record_message_for_analytics(self, message: dict) -> None:
        """Hook called by the instrumented history.append. Captures
        user/assistant text turns into the session transcript. Skips
        tool/system roles — the tool audit log is captured separately
        in _on_tool_outcome."""
        role = message.get("role")
        if role not in ("user", "assistant"):
            return
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return
        self._session_message_count += 1
        self._session_transcript.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _record_tool_call_for_analytics(
        self,
        *,
        name: str,
        args: dict,
        result: object,
    ) -> None:
        """Append one tool invocation to the audit log. Called from
        _on_tool_outcome AFTER the staleness checks pass, so we only
        log the calls that actually mattered."""
        self._session_tool_call_count += 1
        self._session_tool_call_log.append(
            {
                "toolName": name,
                "args": args or {},
                "result": result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def get_session_data(self) -> dict:
        """Snapshot of session-end analytics state. OSS Voqi doesn't
        POST these anywhere by default; hook this from bot.py's outer
        ``finally`` block if you want to ship them to your analytics
        sink."""
        return {
            "session_uuid": self._config.session_uuid,
            "session_started_at": self._session_started_at,
            "transcript": list(self._session_transcript),
            "tool_call_log": list(self._session_tool_call_log),
            "message_count": self._session_message_count,
            "tool_call_count": self._session_tool_call_count,
        }

    # ==================================================================
    # Helpers
    # ==================================================================

    @staticmethod
    def _extract_latest_user_text(frame: LLMContextFrame) -> Optional[str]:
        try:
            messages = frame.context.get_messages()
        except Exception:
            return None
        for msg in reversed(messages or []):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role != "user":
                continue
            content = (
                msg.get("content")
                if isinstance(msg, dict)
                else getattr(msg, "content", None)
            )
            if isinstance(content, str):
                return content.strip() or None
            if isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                joined = " ".join(parts).strip()
                return joined or None
        return None

