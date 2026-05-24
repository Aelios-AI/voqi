"""The agent brain — voice loop, tool dispatch, state machine.

Per turn, the LLM emits a structured output that bundles spoken reply,
tool invocations, and (on user-input turns) verdicts on turn
completeness and whether the visitor switched topics. Tool calls
become widget-side function executions; results stream back and the
agent speaks the follow-up.

Module layout:

  config.py               — InAppRuntimeConfig + InAppTool + Jinja templates
  conversation_history.py — bounded message log with background summarization
  frames.py               — MessageType, InAppMessageFrame, RankedEnvelope
  tool_dispatcher.py      — demonstration-tagged tool dispatch
  screenshot_service.py   — request_screenshot RTVI round-trip + JPEG splicing
  canned_speech.py        — multilingual pre-translated phrases
  agent_output.py         — structured-output Pydantic schemas (per-wake gated)
  processor.py            — InAppAgentProcessor (the FrameProcessor)

The processor uses a priority queue (USER_MESSAGE / TEXT_MESSAGE /
KICKOFF preempt TOOL_BATCH_COMPLETED), five wake modes, background
watchdogs (idle, response-timeout, session-cap, kickoff),
demonstration-scoped tool batches with cancellation on user redirect,
and a streaming-LLM bot turn with read-grace.
"""

from .config import InAppRuntimeConfig, InAppTool
from .conversation_history import InAppConversationHistory
from .frames import InAppMessageFrame, MessageType, RankedEnvelope
from .processor import InAppAgentProcessor
from .tool_dispatcher import ToolDispatcher, ToolDispatchOutcome

__all__ = [
    "InAppAgentProcessor",
    "InAppRuntimeConfig",
    "InAppTool",
    "InAppConversationHistory",
    "MessageType",
    "InAppMessageFrame",
    "RankedEnvelope",
    "ToolDispatcher",
    "ToolDispatchOutcome",
]
