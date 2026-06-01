"""Internal frame + message types for the agent processor.

The vocabulary covers visitor input (USER_MESSAGE / TEXT_MESSAGE),
session lifecycle (KICKOFF), and tool-call returns (TOOL_RESULT,
TOOL_FAILED, TOOL_BATCH_COMPLETED) plus the agent's own internal
events (SCREENSHOT_RESULT, CANNED_SPEECH).

``RankedEnvelope`` exists because :class:`asyncio.PriorityQueue` requires
items to be ordered, and **user messages must preempt pending tool
results** — the agent should always respond to a fresh user turn before
draining queued tool-batch outcomes. Lower numbers run first; ties are
broken by FIFO arrival order via ``_seq``.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Optional


class MessageType(Enum):
    """Vocabulary the priority queue understands.

    The set is intentionally narrow — every type below corresponds to a
    real branch in :class:`InAppAgentProcessor._handle_agent_turn`.
    Adding new types means thinking about priority ordering and
    interruption semantics, not just plumbing.
    """

    USER_MESSAGE = "user_message"          # voice transcript from STT
    TEXT_MESSAGE = "text_message"          # typed text fallback (RTVI client message)
    KICKOFF = "kickoff"                    # widget connected, agent should greet
    TOOL_RESULT = "tool_result"            # one tool-call returned successfully
    TOOL_FAILED = "tool_failed"            # one tool-call returned an error
    TOOL_BATCH_COMPLETED = "tool_batch_completed"  # all tools in a batch resolved
    SCREENSHOT_RESULT = "screenshot_result"  # screenshot the agent requested has
                                           # arrived from the widget. The
                                           # ``data`` payload carries the image
                                           # bytes (image_b64, mime) plus the
                                           # context that triggered the
                                           # request (the original user
                                           # message that prompted the
                                           # decision + the agent's
                                           # acknowledgment speech). Frame
                                           # is put-back-on-interrupt so a
                                           # mid-fetch interruption doesn't
                                           # discard the bytes the widget
                                           # already canvased and shipped.
    CANNED_SPEECH = "canned_speech"        # emit a fixed pre-translated phrase
                                           # via :class:`CannedKey` — used for
                                           # idle warnings, response-timeout
                                           # apologies, batch-ceiling apologies,
                                           # session-idle goodbyes. Lives on
                                           # the same priority queue so EVERY
                                           # visitor-facing utterance flows
                                           # through the same message processor →
                                           # handler → frame-push path. The
                                           # ``data["canned_key"]`` entry
                                           # carries the CannedKey value.


@dataclass
class InAppMessageFrame:
    """The payload the agent processor pulls off the priority queue.

    Three flags worth explaining:

    * ``put_back_when_interrupted``: if True, on user interruption the
      frame is re-enqueued so its work isn't lost. User messages are
      always False (the user already replaced them). Tool batch results
      are True for the *current* demonstration and False for stale ones.

    * ``demonstration_id``: tags the frame to a "demonstration" — the
      conceptual unit the user described (one user request like "show
      me feature A" can spawn many tool calls; a switch to feature B
      cancels them all). Used to purge stale results from the queue
      when the demonstration changes.

    * ``data``: free-form per-type payload (tool name / args / result /
      error / batch contents).
    """

    message: str = ""
    message_type: MessageType = MessageType.USER_MESSAGE
    put_back_when_interrupted: bool = False
    demonstration_id: Optional[str] = None
    data: Optional[dict[str, Any]] = None


@dataclass(order=True)
class RankedEnvelope:
    """Wraps :class:`InAppMessageFrame` for use in an
    :class:`asyncio.PriorityQueue`.

    Ordered by ``(priority, _seq)``. ``frame`` is excluded from
    comparison because dataclass instances aren't naturally ordered and
    we don't need them to be — priority + sequence is enough.

    Recommended priorities (lower = higher precedence):

    * 0 — SYSTEM recovery (rare, urgent)
    * 1 — USER_MESSAGE / TEXT_MESSAGE / KICKOFF / SCREENSHOT_RESULT
    * 3 — TOOL_BATCH_COMPLETED

    The gap between 1 and 3 enforces the rule: a fresh user message
    always preempts pending tool results. They get processed, the agent
    answers, and then the deferred tool results (if still relevant —
    see ``demonstration_id``) come through.
    """

    _counter: ClassVar[itertools.count] = itertools.count()

    priority: int = 0
    _seq: int = field(default_factory=lambda: next(RankedEnvelope._counter))
    frame: Optional[InAppMessageFrame] = field(default=None, compare=False)
