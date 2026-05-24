"""Unified structured output for the agent — one JSON shape per inference.

A single ``with_structured_output`` call returns the complete decision
for this round: what the agent says, what (if any) tools to dispatch,
whether the visitor's turn is finished, whether the active
demonstration continues or pivots, whether a pending batch awaiting
confirmation should be dispatched, and so on. Everything lives on ONE
Pydantic model (:class:`InAppAgentOutput`); the JSON schema fed to the
LLM is built fresh per round (:func:`build_in_app_schema`) and contains
only the fields valid for that round.

Why one unified output (vs. multiple chained calls)
---------------------------------------------------
A single round-trip is faster, cheaper, and easier to reason about
than a chain of pre-classification calls. The schema's per-round shape
is what enforces correctness: instead of trusting the LLM to "not emit
tool_invocations on a kickoff wake", we just don't expose that field
on kickoff wakes. The model literally cannot emit what isn't in its
schema.

Schema gating axes
------------------
The per-round schema is computed from these inputs:

  * ``wake_mode`` — what caused this inference:
      ``user_voice``           — VAD-finalised visitor utterance
      ``user_text``            — typed text from the widget
      ``tool_batch_completed`` — every tool in the in-flight batch resolved
      ``kickoff``              — first inference at session start (greeting only)
      ``screenshot_result``    — a screenshot the agent requested has arrived

  * ``batch_state`` — what the demonstration's batch slot holds:
      ``idle``                  — no batch in flight or pending
      ``in_flight``             — a batch is mid-dispatch
      ``pending_confirmation``  — a batch was proposed and is parked
                                  waiting for the visitor to OK it

  * ``mode`` — the session-wide mode chosen at /start:
      ``action`` — full agent: speech + tools + demonstrations
      ``guide``  — read-only screen-aware: speech + ghost cursor only

  * ``tools``                 — list of registered tools; empty list →
                                ``tool_invocations`` is omitted entirely.
  * ``idle_stage_two_armed``  — True while the 60-second idle grace is
                                running after IDLE_WARNING was spoken.
  * ``original_wake_mode``    — on ``screenshot_result`` wakes, the wake
                                that originally triggered the screenshot
                                fetch (used in guide mode to decide
                                whether the voice gates carry over).

Two short-circuits collapse the schema for special wakes:
  * **Kickoff** exposes ONLY ``speech`` — the agent's job is to greet,
    no tools, no demo state, no relevance gate.
  * **Guide mode** exposes ONLY ``speech`` + ``point_to`` (+ voice
    gates on voice-originated wakes). No demonstrations, no tools, no
    batch confirmation, no screenshot decision — the processor force-
    fetches a screenshot on every guide-mode user wake before this
    inference runs, so by the time the LLM sees the wake it already
    has the visual context.

Fields
------
``is_message_relevant`` — relevance gate (voice wakes only)
    The mic is open passively on a customer's website; audio may be a
    side-conversation, background fragment, or random noise. On voice
    wakes the LLM decides FIRST whether the utterance was addressed to
    the agent. If ``off_topic``, the rest of this turn is suppressed
    (empty speech, no tools, no demo change) and the system treats the
    wake as silence for idle-timer purposes. Typed text is always
    ``relevant`` and skips the gate.

``user_turn_status`` — completeness gate (voice wakes only)
    ``complete`` / ``incomplete_short`` (paused mid-clause) /
    ``incomplete_long`` (trailed off). When incomplete, output is
    suppressed and the agent waits for the visitor to finish.

``demonstration_action`` — what to do with the active demonstration
    ``continue``    — leave demo state alone (default).
    ``start_new``   — interrupt any active demo and start a fresh one;
                      cancels its in-flight + pending invocations.
                      ``demonstration_name`` must be set.
    ``end_current`` — end the active demo without starting another;
                      cancels its in-flight + pending invocations.
    Per-wake enum: ``tool_batch_completed`` wakes drop ``start_new``
    (no fresh user signal motivates a switch).

``demonstration_name`` — short label for a freshly-started demo
    Required iff ``demonstration_action == 'start_new'``.

``idle_warning_resolution`` — armed only during the 60s idle grace
    After IDLE_WARNING ("are you still there?") is spoken, the
    visitor's next utterance is classified here. ``end_session``
    closes the session immediately; ``continue_session`` resets both
    the 60s grace and the upstream idle timer.

``pending_batch_resolution`` — only when a batch awaits confirmation
    ``accept``       — dispatch the held batch as-is.
    ``replace``      — drop it; dispatch this turn's ``tool_invocations``
                       as the new batch (which itself may need
                       confirmation again).
    ``keep_waiting`` — visitor said something unrelated; leave the
                       batch state alone and just answer them.
    There is deliberately NO plain ``decline``. Declining without
    proposing a replacement OR ending the demo would leave the
    demonstration stuck — no batch in flight, nothing to trigger the
    next inference. To decline the visitor must either ``replace``
    with a different batch or set
    ``demonstration_action='end_current'``.

``speech`` — what the agent says (``""`` means stay silent).

``tool_invocations`` — tools to fire IN PARALLEL this turn
    All invocations in one batch are independent — they do NOT see
    each other's results, and you cannot rely on ordering. Dependent
    work must be split across turns: prerequisite this turn, dependent
    on the next inference after the prerequisite resolves. The next
    inference (a ``tool_batch_completed`` wake) fires only after every
    invocation in the batch has reported back.

    Named ``tool_invocations`` (not ``tool_calls``) deliberately: a
    field literally named ``tool_calls`` biases the model toward
    OpenAI's native function-calling protocol, which we are NOT using
    — we drive structured output and dispatch the resulting list
    ourselves through the widget. Mixing the two mental models
    confuses the model.

    Exposed only when the agent has tools registered AND one of:
      * a user-input wake (``start_new`` may interrupt the current
        batch and replace it),
      * a ``tool_batch_completed`` wake (queue the next batch under
        the same demo),
      * a ``screenshot_result`` wake (the agent has visual context now
        and can act), OR
      * a batch is pending confirmation (``replace`` may swap it).

``decision_to_request_screenshot`` — screenshot request gate (user-input wakes only)
    True iff answering REQUIRES seeing the visitor's screen and the
    request is ambiguous without it (e.g. "what does this mean?",
    "remove this one"). When True, this turn becomes a brief
    acknowledgment ("let me take a look") with no tools and no demo
    change; the next inference is a ``screenshot_result`` wake
    carrying the captured image plus the
    ``screenshot_request_context`` the LLM wrote on this turn. Not
    exposed on ``screenshot_result`` wakes — once the agent has the
    screenshot in hand it must answer; it cannot loop.

``screenshot_request_context`` — note-to-future-self for the screenshot trip
    Required when ``decision_to_request_screenshot`` is True. 1–2
    sentences naming the visual element and what the LLM intends to
    do on the next inference. By the time the image arrives other
    turns may have intervened; this string is the LLM's self-contained
    brief.

``point_to`` — guide-mode cursor hint (guide mode only)
    Normalized (0–1) coordinates + a short label. The widget floats a
    ghost cursor + label at those coordinates for ~20s. The agent
    CANNOT click — only show where to click; the spoken reply should
    still describe what to do.

Field availability matrix
-------------------------
At a glance, which fields are exposed on which wakes (action mode):

    Field                              kickoff  user_voice  user_text  tool_batch_completed  screenshot_result
    speech                               ✓          ✓          ✓             ✓                    ✓
    demonstration_action                            ✓          ✓             ✓                    ✓
    demonstration_name                              ✓          ✓                                  ✓   (only when 'start_new' in enum)
    is_message_relevant                             ✓
    user_turn_status                                ✓
    idle_warning_resolution                         ✓¹         ✓¹                                 ✓¹  (¹ only if idle stage 2 armed)
    pending_batch_resolution                        ✓²         ✓²            ✓²                   ✓²  (² only if batch_state == pending_confirmation)
    decision_to_request_screenshot                  ✓          ✓
    screenshot_request_context                      ✓          ✓
    tool_invocations                                ✓³         ✓³            ✓³                   ✓³  (³ only if tools registered)

Guide mode collapses this to: ``speech`` + ``point_to`` (+ voice gates
on voice-originated wakes; + ``idle_warning_resolution`` when armed).

The :class:`InAppAgentOutput` Pydantic model carries every field as
``Optional`` because the model itself is shared across all wake/state
combinations. The per-round JSON schema is what enforces "this field
must be present" or "this field must be absent" for any given round.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

from .config import InAppTool

WakeMode = Literal[
    "user_voice",
    "user_text",
    "tool_batch_completed",
    "kickoff",
    "screenshot_result",
]
BatchState = Literal["idle", "in_flight", "pending_confirmation"]


class InAppPointTo(BaseModel):
    """One coordinate-point hint the agent can ship to the widget in
    guide mode. Coordinates are normalized to the screenshot the agent
    saw on this turn — ``(0, 0)`` is the top-left, ``(1, 1)`` is the
    bottom-right. The widget multiplies by its actual viewport size to
    place the ghost cursor.

    Normalized coords (rather than raw pixels) survive devicePixelRatio
    differences, browser zoom, and screenshots that were downscaled
    server-side. The screenshot the LLM sees and the viewport the
    cursor lands on are both tied to the visitor's current page state,
    so the same normalized fraction maps cleanly to either one.
    """

    x: float = Field(
        description=(
            "Horizontal coordinate in [0, 1] — fraction of the "
            "screenshot's width from the left edge."
        ),
        ge=0.0,
        le=1.0,
    )
    y: float = Field(
        description=(
            "Vertical coordinate in [0, 1] — fraction of the "
            "screenshot's height from the top edge."
        ),
        ge=0.0,
        le=1.0,
    )
    label: str = Field(
        description=(
            "Short label that sits next to the cursor (e.g. 'Click "
            "here', 'Open Settings'). Keep under ~30 characters; this "
            "is the visual hint, not the spoken explanation."
        ),
        max_length=80,
    )


class InAppToolInvocation(BaseModel):
    """One tool invocation the LLM wants the widget to perform.

    Named ``Invocation`` rather than ``Call`` deliberately — see the
    module docstring on why we avoid ``tool_call``-flavoured naming
    in our structured output.
    """

    name: str = Field(
        description="Name of one of the tools registered for this agent."
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "JSON object matching the tool's parameters schema. Pass an "
            "empty object if the tool takes no arguments."
        ),
    )


class InAppAgentOutput(BaseModel):
    """Single unified output from one LLM round.

    Fields are all ``Optional`` here at the model level because each
    round's schema only requires a subset (see
    :func:`build_in_app_schema`). The processor branches on which
    fields are populated.
    """

    is_message_relevant: Optional[Literal["relevant", "off_topic"]] = Field(
        default=None,
        description=(
            "Decided BEFORE everything else on user wakes. The widget is "
            "passively listening on a customer's website — the visitor "
            "may be in a room with other people, or speaking to someone "
            "off-screen. Only respond when the message is actually for "
            "you: a question about the software, a request to control "
            "the software, or a continuation of either. Anything else "
            "(small talk to another person, background chatter, partial "
            "phrases overheard from the room) is 'off_topic' and must "
            "be IGNORED — speech='', tool_invocations=[], "
            "demonstration_action='continue', pending_batch_resolution="
            "'keep_waiting' (if present). When 'off_topic', the rest of "
            "this output's reasoning is moot. Default to 'relevant' "
            "when in doubt about a clear product question."
        ),
    )

    user_turn_status: Optional[Literal["complete", "incomplete_short", "incomplete_long"]] = Field(
        default=None,
        description=(
            "Only present when the wake reason is a voice user turn AND "
            "is_message_relevant='relevant'. "
            "incomplete_short = paused mid-clause; incomplete_long = "
            "trailed off / distracted. When incomplete, speech MUST be "
            "an empty string, demonstration_action MUST be 'continue', "
            "tool_invocations MUST be empty, and pending_batch_resolution "
            "(if present) MUST be 'keep_waiting'."
        ),
    )

    demonstration_action: Literal["continue", "start_new", "end_current"] = Field(
        default="continue",
        description=(
            "What to do with the active demonstration:\n"
            "  continue     — leave the demonstration state alone. Default.\n"
            "  start_new    — start a brand new demonstration. If one is "
            "already active it is interrupted: any in-flight or pending "
            "tool invocations are cancelled. demonstration_name MUST be set.\n"
            "  end_current  — end the active demonstration (cancels its "
            "in-flight or pending tool invocations) without starting another."
        ),
    )

    demonstration_name: Optional[str] = Field(
        default=None,
        description=(
            "Short human-readable name for the demonstration being "
            "started. REQUIRED when demonstration_action is 'start_new'. "
            "Ignored otherwise."
        ),
    )

    idle_warning_resolution: Optional[Literal["end_session", "continue_session"]] = Field(
        default=None,
        description=(
            "Present ONLY when the visitor was just asked the idle "
            "check-in ('are you still there?') and the 60-second grace "
            "period is currently running. Decide what the visitor's "
            "current message means in that context:\n"
            "  end_session       — the visitor confirmed they're done "
            "(e.g. 'yeah you can close it', 'no I'm good thanks'). "
            "The session will end IMMEDIATELY.\n"
            "  continue_session  — the visitor wants to keep going OR "
            "said something else that's a valid product question / "
            "command. The grace period and the 3-minute timer both "
            "reset; treat the rest of this turn as a normal request."
        ),
    )

    pending_batch_resolution: Optional[Literal["accept", "replace", "keep_waiting"]] = Field(
        default=None,
        description=(
            "Present ONLY when there is a batch awaiting the visitor's "
            "confirmation. How this turn resolves it:\n"
            "  accept        — dispatch the held batch.\n"
            "  replace       — drop the held batch, dispatch this turn's "
            "tool_invocations as the new batch (which itself may need "
            "confirmation again).\n"
            "  keep_waiting  — visitor said something unrelated; don't "
            "touch the batch state, just answer them and we'll ask for "
            "confirmation again.\n"
            "There is deliberately no 'decline' option. If the visitor "
            "declines, you must EITHER propose a different batch via "
            "'replace', OR set demonstration_action='end_current' to "
            "end the demonstration. Plain decline without a replacement "
            "would leave the demonstration stuck with no batch in "
            "flight and no next inference trigger."
        ),
    )

    speech: str = Field(
        default="",
        description=(
            "What the agent says to the visitor. Empty string means "
            "stay silent."
        ),
    )

    tool_invocations: List[InAppToolInvocation] = Field(
        default_factory=list,
        description=(
            "Tool invocations to fire IN PARALLEL this turn. All "
            "invocations in the same batch are independent — they do "
            "NOT see each other's results, and you cannot rely on one "
            "finishing before another starts. If the work has a logical "
            "dependency (e.g. log in first, THEN create the resource), "
            "split it across turns: emit only the prerequisite tools "
            "this turn, and queue the dependent ones on the next "
            "inference after the prerequisite results land. All "
            "invocations in this batch share one demonstration id; the "
            "next inference fires only after the entire batch resolves. "
            "Empty list = no tools this turn."
        ),
    )

    decision_to_request_screenshot: Optional[bool] = Field(
        default=None,
        description=(
            "Set to True ONLY when answering the visitor REQUIRES seeing "
            "their current screen and the request is ambiguous without "
            "it (e.g. 'what does this mean?', 'how do I remove this?', "
            "'why is this red?'). When you set this True, the rest of "
            "this turn is a brief acknowledgment ONLY — speech is a "
            "short natural sentence telling the visitor you're going "
            "to look at their screen, tool_invocations MUST be empty, "
            "demonstration_action MUST be 'continue', and "
            "pending_batch_resolution (if exposed) MUST be "
            "'keep_waiting'. The system will then capture a screenshot "
            "from the widget, attach it as image context, and re-invoke "
            "you with the screenshot + the screenshot_request_context "
            "you wrote, so you can answer / act with full vision of what "
            "the visitor was looking at. Default False — most requests "
            "don't need the screen (e.g. 'create a task called X' is "
            "unambiguous; 'list my open tickets' is unambiguous)."
        ),
    )

    screenshot_request_context: Optional[str] = Field(
        default=None,
        description=(
            "Set ONLY when decision_to_request_screenshot=True. Write "
            "1–2 sentences describing what the visitor referred to and "
            "what you'll be looking for in the screenshot. Treat this "
            "as a note to your future self: by the time the image "
            "arrives, several other turns may have happened and the "
            "raw conversation history may be unwieldy — this string is "
            "your self-contained brief. Be specific. Examples:\n"
            "  • 'Visitor pointed at a red banner and asked what it "
            "means. I'm checking the page for an error message and "
            "deciding whether they need to retry an action or contact "
            "support.'\n"
            "  • 'Visitor said \"how do I remove this one?\" without "
            "naming what. I'm identifying which item on screen they're "
            "referring to (a label, an assignee, or a sprint chip) so "
            "I can pick the right tool to call.'\n"
            "Leave null on every turn where decision_to_request_"
            "screenshot is False or not set."
        ),
    )

    point_to: Optional[InAppPointTo] = Field(
        default=None,
        description=(
            "GUIDE MODE ONLY. Set when the answer to the visitor's "
            "request is 'click / look at this specific spot on the "
            "screen'. Provide normalized coordinates ((0,0) top-left, "
            "(1,1) bottom-right) and a short on-screen label; the "
            "widget will float a ghost cursor + label there for ~20s. "
            "Leave null on turns that need no pointing (chit-chat, "
            "explanations, refusals). The agent CANNOT execute the "
            "click — only show where to click; the spoken response "
            "should still describe what to do."
        ),
    )


def build_in_app_schema(
    *,
    wake_mode: WakeMode,
    batch_state: BatchState,
    tools: list[InAppTool],
    idle_stage_two_armed: bool = False,
    mode: Literal["action", "guide"] = "action",
    original_wake_mode: Optional[Literal["user_voice", "user_text"]] = None,
) -> dict:
    """Build the JSON schema passed to ``with_structured_output``.

    The schema's shape encodes the two-trigger rule and the pending-
    confirmation state machine so the LLM literally cannot emit fields
    that don't apply this round.

    ``tool_invocations`` is exposed only when:
      * The agent has tools registered AND
      * Either: this is a user-input wake (the LLM may use ``start_new``
        to interrupt and dispatch a fresh batch), OR
      * Either: this is a tool-batch-completed wake (the LLM may queue
        the next batch under the same demo), OR
      * Either: a batch is pending confirmation and the LLM may choose
        ``replace`` to swap it.

    ``pending_batch_resolution`` is exposed ONLY when ``batch_state ==
    "pending_confirmation"``.

    ``user_turn_status`` is required only on voice user-input wakes.
    """
    # ── Speech-only short-circuit for kickoff greeting ─────────────
    # Kickoff is the bot's session-start greeting. The agent's only
    # job is to say hi to the visitor appropriately (brand / persona
    # are already in the static prefix). No demo state to act on,
    # no tools to dispatch — the schema exposes ONLY ``speech`` so
    # the LLM literally cannot emit anything else.
    if wake_mode == "kickoff":
        return {
            "type": "object",
            "properties": {
                "speech": {
                    "type": "string",
                    "description": (
                        "Greet the visitor at session start. ONE or "
                        "two short sentences max — friendly, natural, "
                        "and contextual to the agent persona / "
                        "software shown in the static prefix above."
                    ),
                },
            },
            "required": ["speech"],
        }

    # ── Guide mode short-circuit ──────────────────────────────────────
    # Guide mode is a much smaller surface than action mode: no tools,
    # no demonstrations, no batch confirmation, no decision-to-request-
    # screenshot (the processor force-fetches a screenshot on every
    # user wake before this LLM call runs). The only outputs the
    # agent produces are speech + an optional ``point_to`` for the
    # ghost cursor. Relevance + completeness gates carry over for
    # voice wakes so we don't spam cursors on background chatter.
    if mode == "guide":
        properties: dict[str, dict] = {
            "speech": {
                "type": "string",
                "description": (
                    "Spoken reply to the visitor. Empty string only "
                    "if the visitor's input is off-topic / incomplete "
                    "(see is_message_relevant + user_turn_status). "
                    "When you point with point_to, the spoken reply "
                    "should still describe what to do — the cursor "
                    "is a visual hint, not a substitute for the "
                    "explanation."
                ),
            },
            "point_to": {
                "type": ["object", "null"],
                "description": (
                    "Set when the answer is 'click / look at this "
                    "specific spot on the visitor's screen'. The "
                    "widget will float a ghost cursor + label at the "
                    "given coordinates for ~20s. Coordinates are "
                    "normalized to the screenshot you were given on "
                    "this turn: (0, 0) is the top-left, (1, 1) is "
                    "the bottom-right. Leave null on turns that need "
                    "no pointing — chit-chat, explanations, refusals, "
                    "or when the target is OFF-SCREEN (in that case "
                    "ask the visitor to scroll first)."
                ),
                "properties": {
                    "x": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": (
                            "Horizontal coordinate as a fraction of "
                            "the screenshot width — 0 is the left "
                            "edge, 1 is the right edge."
                        ),
                    },
                    "y": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": (
                            "Vertical coordinate as a fraction of "
                            "the screenshot height — 0 is the top "
                            "edge, 1 is the bottom edge."
                        ),
                    },
                    "label": {
                        "type": "string",
                        "maxLength": 80,
                        "description": (
                            "Short on-screen label next to the "
                            "cursor (e.g. 'Click here', 'Open "
                            "Settings'). Keep it ~30 chars or fewer."
                        ),
                    },
                },
                "required": ["x", "y", "label"],
            },
        }
        required: list[str] = ["speech", "point_to"]

        # Voice-wake relevance + completeness gates carry over from
        # action mode — same off-topic / incomplete-utterance hazards
        # apply to guide mode (the mic is open passively in both).
        # Important: in guide mode the LLM never runs at user_voice —
        # the processor short-circuits user wakes into a screenshot
        # fetch and only inferences at screenshot_result. So the gates
        # are exposed at screenshot_result wakes whose original wake
        # was a voice utterance (carried via ``original_wake_mode``).
        gates_apply = wake_mode == "user_voice" or (
            wake_mode == "screenshot_result"
            and original_wake_mode == "user_voice"
        )
        if gates_apply:
            properties["is_message_relevant"] = {
                "type": "string",
                "enum": ["relevant", "off_topic"],
                "description": (
                    "DECIDE THIS FIRST on voice-originated turns. "
                    "'relevant' = audio said TO you; 'off_topic' = "
                    "side-conversation, ambient noise, indistinct "
                    "fragment. When 'off_topic' speak a brief WARM "
                    "ACKNOWLEDGMENT (one short sentence, 8–18 words) "
                    "so the visitor knows you heard something but "
                    "understood it wasn't aimed at you, and set "
                    "point_to=null. NEVER leave speech empty on "
                    "off_topic — silent is wrong."
                ),
            }
            required.append("is_message_relevant")
            properties["user_turn_status"] = {
                "type": "string",
                "enum": ["complete", "incomplete_short", "incomplete_long"],
                "description": (
                    "Was the visitor's last utterance a finished "
                    "thought? Default 'complete'. If incomplete_*, "
                    "speak a brief warm nudge and set point_to=null."
                ),
            }
            required.append("user_turn_status")

        if idle_stage_two_armed and wake_mode in (
            "user_voice", "user_text", "screenshot_result",
        ):
            properties["idle_warning_resolution"] = {
                "type": "string",
                "enum": ["end_session", "continue_session"],
                "description": (
                    "The visitor was JUST asked 'are you still "
                    "there?' (60-second grace running). 'end_session' "
                    "if their reply confirms they're done; "
                    "'continue_session' otherwise (resets the timers)."
                ),
            }
            required.append("idle_warning_resolution")

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    # Per-wake demonstration_action enum. Values only appear when the
    # wake makes them sensible:
    #   * tool_batch_completed → no fresh user signal motivates a switch,
    #     so 'start_new' is forbidden. The agent's choices are 'continue'
    #     (queue more work or just speak) or 'end_current' (goal met).
    #   * user_voice / user_text → all three valid.
    if wake_mode in ("user_voice", "user_text", "screenshot_result"):
        # screenshot_result is treated like a user-input wake here:
        # the agent now has the visual context for the visitor's
        # original ask, so it can start a fresh demo to act on what
        # was on screen (e.g. "remove this red banner the user
        # pointed at").
        action_enum = ["continue", "start_new", "end_current"]
        action_desc = (
            "continue = leave demo state alone. start_new = "
            "interrupt any active demo + start a fresh one "
            "(demonstration_name required). end_current = end the "
            "active demo (cancels its in-flight or pending tool "
            "invocations) without starting another."
        )
    else:  # tool_batch_completed
        action_enum = ["continue", "end_current"]
        action_desc = (
            "continue = stay in this demonstration; either queue another "
            "batch or just speak. end_current = the demonstration's goal "
            "is satisfied (or the batch results show it cannot be "
            "satisfied), wrap up. start_new is NOT available on this "
            "wake — there is no fresh user signal to motivate a switch."
        )

    properties: dict[str, dict] = {
        "speech": {
            "type": "string",
            "description": "Spoken reply. Empty string when staying silent.",
        },
        "demonstration_action": {
            "type": "string",
            "enum": action_enum,
            "description": action_desc,
        },
    }
    required: list[str] = ["speech", "demonstration_action"]

    # demonstration_name is meaningful only when 'start_new' is in the
    # enum for this wake. On other wakes it's omitted from the schema
    # entirely so the LLM doesn't waste a token-prediction on a null
    # field it can never use.
    if "start_new" in action_enum:
        properties["demonstration_name"] = {
            "type": ["string", "null"],
            "description": (
                "Short label for the demo. REQUIRED when "
                "demonstration_action='start_new'. Null otherwise."
            ),
        }
        required.append("demonstration_name")

    # Relevance gate is the FIRST decision on a VOICE user wake. The
    # mic is open passively — audio may have been spoken to someone
    # else in the room, picked up as background, or an indistinct
    # fragment that wasn't directed at the agent at all. Only
    # voice-wake utterances need this filter; typed text is always
    # explicitly directed at the agent and skips the gate. Tool /
    # system wakes don't have a visitor utterance.
    if wake_mode == "user_voice":
        properties["is_message_relevant"] = {
            "type": "string",
            "enum": ["relevant", "off_topic"],
            "description": (
                "DECIDE THIS FIRST on voice wakes. 'relevant' = the "
                "audio was said TO you (product question, software "
                "command, continuation of your exchange, OR an "
                "out-of-scope request asked of you that you'll refuse "
                "via the templates). 'off_topic' = the audio was NOT "
                "addressed to you (talking to a colleague, side "
                "conversation, ambient noise, indistinct fragments). "
                "When 'off_topic', set speech='', tool_invocations=[], "
                "demonstration_action='continue', "
                "pending_batch_resolution='keep_waiting' (if present); "
                "the system will treat the turn as no activity for the "
                "idle timer."
            ),
        }
        required.append("is_message_relevant")

    if wake_mode == "user_voice":
        properties["user_turn_status"] = {
            "type": "string",
            "enum": ["complete", "incomplete_short", "incomplete_long"],
            "description": (
                "Only meaningful when is_message_relevant='relevant'. "
                "Was the visitor's last utterance a finished thought? "
                "Default to 'complete' when in doubt. If incomplete_*, "
                "set speech='', tool_invocations=[], demonstration_action="
                "'continue', pending_batch_resolution='keep_waiting' "
                "(if present)."
            ),
        }
        required.append("user_turn_status")

    # idle_warning_resolution is exposed only when stage 2 of the
    # idle-session timer is armed AND the visitor just spoke / typed.
    # The agent must classify whether they're confirming end-of-session
    # or continuing.
    if idle_stage_two_armed and wake_mode in ("user_voice", "user_text"):
        properties["idle_warning_resolution"] = {
            "type": "string",
            "enum": ["end_session", "continue_session"],
            "description": (
                "The visitor was JUST asked 'are you still there?' "
                "(60-second grace running). Set 'end_session' if their "
                "current message confirms they're done — session "
                "closes immediately. Set 'continue_session' if they "
                "want to keep going OR said anything else valid; both "
                "the grace and the 3-minute timer reset."
            ),
        }
        required.append("idle_warning_resolution")

    if batch_state == "pending_confirmation":
        properties["pending_batch_resolution"] = {
            "type": "string",
            "enum": ["accept", "replace", "keep_waiting"],
            "description": (
                "How this turn resolves the batch awaiting the "
                "visitor's confirmation. accept dispatches it; replace "
                "drops it and dispatches this turn's tool_invocations "
                "as the new batch; keep_waiting changes nothing (chit-chat "
                "/ clarifying question). No plain 'decline' option — if "
                "the visitor declines, either use 'replace' with a "
                "different batch, or set "
                "demonstration_action='end_current' to end the demo."
            ),
        }
        required.append("pending_batch_resolution")

    # decision_to_request_screenshot — exposed on user-input wakes
    # only. The visitor's utterance is what may carry an ambiguous
    # reference to on-screen content; tool-batch and screenshot-result
    # wakes are agent-internal and shouldn't gate on this. Kickoff has
    # no visitor utterance. The schema-builder for screenshot_result
    # wakes specifically does NOT expose this field — once the agent
    # has the screenshot in hand, it must answer; it can't loop.
    if wake_mode in ("user_voice", "user_text"):
        properties["decision_to_request_screenshot"] = {
            "type": "boolean",
            "description": (
                "Set True ONLY if the visitor's request is ambiguous "
                "without seeing their current screen — e.g. 'what does "
                "this mean?', 'how do I remove this?', 'why is this "
                "red?'. When True, treat this turn as a brief "
                "acknowledgment: speech announces 'I'll take a look at "
                "your screen', tool_invocations=[], "
                "demonstration_action='continue', "
                "pending_batch_resolution='keep_waiting' (if present), "
                "AND screenshot_request_context MUST be filled in "
                "(your note about what to look at when the screenshot "
                "arrives). Default False — unambiguous requests like "
                "'create a task called X' or 'show me my orders' do "
                "not need vision and the agent should just act."
            ),
        }
        required.append("decision_to_request_screenshot")
        # Required-when-true companion to decision_to_request_screenshot.
        # The schema can't easily encode "required iff another field is
        # true", so we expose it always-nullable and rely on the prompt
        # + the priority gate's runtime check (it'll log a warning and
        # fall back to the raw utterance if the LLM forgets it).
        properties["screenshot_request_context"] = {
            "type": ["string", "null"],
            "description": (
                "Required when decision_to_request_screenshot=True. "
                "1–2 sentences explaining what the visitor referred to "
                "and what you'll be looking for in the captured "
                "screenshot — your note to your future self when the "
                "image arrives on the next inference. Be specific: "
                "name the visual element if you can, the visitor's "
                "intent, and what you'd act on next. Leave null when "
                "decision_to_request_screenshot is False."
            ),
        }
        required.append("screenshot_request_context")

    # tool_invocations visibility — the two-trigger rule plus the pending
    # ``replace`` exception. Field is named ``tool_invocations`` (not
    # ``tool_calls``) to avoid the LLM bleeding OpenAI native
    # function-calling semantics into our app-level dispatch.
    # ``screenshot_result`` is added as a wake that's allowed to
    # dispatch tools — by the time the agent has the screenshot, it
    # has the full picture and can take normal action.
    tool_invocations_allowed = bool(tools) and (
        wake_mode in (
            "user_voice", "user_text", "tool_batch_completed",
            "screenshot_result",
        )
        or batch_state == "pending_confirmation"
    )
    if tool_invocations_allowed:
        tool_names = [t.name for t in tools]
        properties["tool_invocations"] = {
            "type": "array",
            "description": (
                "Tools to invoke this turn. All run IN PARALLEL under "
                "the active demonstration id — they are independent "
                "and cannot depend on each other's results. If you "
                "need step B to use step A's output, emit only A this "
                "turn and queue B on the next inference (after A's "
                "result lands). The next inference fires only after "
                "every tool in this batch resolves. Empty array if no "
                "tool is needed."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": tool_names},
                    "arguments": {
                        "type": "object",
                        "description": (
                            "JSON args matching the tool's schema. {} "
                            "for no-argument tools."
                        ),
                    },
                },
                "required": ["name", "arguments"],
            },
        }
        required.append("tool_invocations")

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
