"""Conversation history with bounded window + background summarization.

Voice sessions can run long enough that the raw message log overflows
the LLM's working context. Strategy:

  1. Append all messages normally as the conversation flows.
  2. When the log grows past ``max_window`` messages, a background
     ``asyncio.Task`` is scheduled to summarize the oldest
     ``summarize_chunk`` messages.
  3. While that summary is in flight, new messages keep landing on the
     tail (they're not summarized — only the oldest chunk is).
  4. When the summary completes, the summarized messages are atomically
     replaced by a single ``{"role": "system", "content": "[Earlier
     conversation summary] …"}`` entry. If a previous summary entry
     already exists at the head, the new summary *extends* it (we don't
     keep stacking summaries — one is enough).

The bot's LLM call always reads from :py:meth:`get_messages_for_inference`,
which returns a fresh list including the head summary (if any) plus the
live tail. The summary task is fire-and-forget from the caller's
perspective — if it takes a while, the next LLM call simply sees the
unsummarized history.

Cancellation: this object owns nothing it can't cancel. If the processor
shuts down, ``shutdown()`` cancels any in-flight summary task.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from loguru import logger

_SUMMARY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are summarizing the early portion of a voice conversation between an "
            "AI in-app assistant and a user on a customer's website. The summary "
            "will replace these messages in the assistant's working memory so it can "
            "stay grounded without re-reading every utterance.\n\n"
            "Rules:\n"
            "- Preserve facts the user shared about themselves, their goal, and any "
            "  decisions they made.\n"
            "- Preserve which features/tools the assistant has already shown.\n"
            "- Drop pleasantries, filler, and re-asked questions.\n"
            "- Write in third person, present tense, ~150 words max.\n"
            "- If a previous summary is provided, integrate it — do not stack two "
            "  summaries; produce one combined summary.",
        ),
        (
            "human",
            "Previous summary (may be empty):\n{previous_summary}\n\n"
            "Messages to summarize (oldest first):\n{messages}\n\n"
            "Produce the combined summary now.",
        ),
    ]
)


class InAppConversationHistory:
    """Bounded message log with opportunistic summarization."""

    SUMMARY_ROLE = "system"
    SUMMARY_PREFIX = "[Earlier conversation summary] "

    def __init__(
        self,
        *,
        system_message: str,
        max_window: int = 30,
        summarize_chunk: int = 16,
        summary_model: str = "gemini-2.5-flash",
    ):
        if summarize_chunk >= max_window:
            raise ValueError("summarize_chunk must be smaller than max_window")
        self._system_message = system_message
        self._max_window = max_window
        self._summarize_chunk = summarize_chunk
        self._messages: list[dict] = []
        self._summary: Optional[str] = None
        self._summarizing_lock = asyncio.Lock()
        self._summary_task: Optional[asyncio.Task] = None

        self._summary_chain = _SUMMARY_PROMPT | ChatGoogleGenerativeAI(
            model=summary_model,
        )

    # ------------------------------------------------------------------
    # Append API — called from the processor's turn handlers.
    # ------------------------------------------------------------------

    def append(self, message: dict) -> None:
        """Append a message. Triggers summarization if the window is over.

        ``message`` is the raw OpenAI-shape dict (``{"role": ..., "content":
        ...}`` plus optional ``tool_calls`` / ``tool_call_id``).
        """
        self._messages.append(message)
        if len(self._messages) > self._max_window:
            self._maybe_kick_summary()

    def append_user(self, content: str) -> None:
        self.append({"role": "user", "content": content})

    def append_assistant_text(self, content: str) -> None:
        self.append({"role": "assistant", "content": content})

    def append_assistant_tool_calls(self, content: str, tool_calls: list[dict]) -> None:
        self.append(
            {
                "role": "assistant",
                "content": content or "",
                "tool_calls": tool_calls,
            }
        )

    def append_tool_result(self, *, tool_call_id: str, content: str) -> None:
        self.append({"role": "tool", "tool_call_id": tool_call_id, "content": content})

    # ------------------------------------------------------------------
    # Read API — what the LLM sees.
    # ------------------------------------------------------------------

    def get_messages_for_inference(self) -> list[dict]:
        """Return the messages the LLM should see for the next call.

        Always: ``[ system_message, (summary if any), …live tail… ]``.

        Used by tests + legacy callers. The processor itself renders
        history INSIDE its single-template prompt now (see
        :meth:`get_log_for_template`); this accessor stays for
        readability and historical-pattern compatibility.
        """
        out: list[dict] = [{"role": "system", "content": self._system_message}]
        if self._summary:
            out.append(
                {
                    "role": self.SUMMARY_ROLE,
                    "content": self.SUMMARY_PREFIX + self._summary,
                }
            )
        out.extend(self._messages)
        return out

    def get_log_for_template(self) -> tuple[Optional[str], list[dict]]:
        """Return the rolling conversation log the agent-turn template
        renders inline: ``(summary_or_none, [user/assistant messages])``.

        No system-prompt prefix here — the template renders all the
        instruction / state context itself.
        """
        return self._summary, list(self._messages)

    @property
    def message_count(self) -> int:
        return len(self._messages)

    @property
    def has_summary(self) -> bool:
        return self._summary is not None

    # ------------------------------------------------------------------
    # Summarization machinery.
    # ------------------------------------------------------------------

    def _maybe_kick_summary(self) -> None:
        if self._summary_task is not None and not self._summary_task.done():
            return  # one in flight already
        if len(self._messages) <= self._summarize_chunk:
            return
        chunk = self._messages[: self._summarize_chunk]
        # Schedule summarization; the task will swap chunk out for a summary
        # entry on success, leave the log alone on failure. Fire-and-forget
        # from the caller's perspective.
        self._summary_task = asyncio.get_running_loop().create_task(
            self._summarize_chunk_task(chunk),
            name="in-app-summary",
        )

    async def _summarize_chunk_task(self, chunk: list[dict]) -> None:
        try:
            async with self._summarizing_lock:
                rendered = "\n".join(
                    f"{m.get('role', '?').upper()}: {self._render_content(m)}"
                    for m in chunk
                )
                result = await self._summary_chain.ainvoke(
                    {
                        "previous_summary": self._summary or "(none)",
                        "messages": rendered,
                    }
                )
                new_summary = getattr(result, "content", str(result)).strip()
                if not new_summary:
                    logger.warning("[in-app-history] empty summary; skipping swap")
                    return

                # Swap atomically. ``self._messages`` may have grown while we
                # were summarizing — that's fine. We only remove the prefix
                # we summarized; the rest of the tail is preserved as-is.
                if len(self._messages) >= self._summarize_chunk:
                    self._messages = self._messages[self._summarize_chunk :]
                    self._summary = new_summary
                    logger.info(
                        f"[in-app-history] summarized {self._summarize_chunk} messages; "
                        f"summary len={len(new_summary)} chars; tail={len(self._messages)}"
                    )
        except asyncio.CancelledError:
            logger.debug("[in-app-history] summary task cancelled")
            raise
        except Exception as e:
            logger.warning(f"[in-app-history] summarization failed: {e}")
        finally:
            self._summary_task = None

    @staticmethod
    def _render_content(msg: dict) -> str:
        c = msg.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
            return " ".join(parts)
        return str(c) if c is not None else ""

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        if self._summary_task is not None and not self._summary_task.done():
            self._summary_task.cancel()
            try:
                await self._summary_task
            except (asyncio.CancelledError, Exception):
                pass
            self._summary_task = None
