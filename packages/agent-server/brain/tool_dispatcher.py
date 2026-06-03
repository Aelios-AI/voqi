"""Background tool-call orchestration with cancellable demonstrations.

A **demonstration** is the conceptual grouping of every tool call the
agent fires in service of a single user request. Inside a demo, each
parallel batch of tool calls carries its own ``batch_id`` so the
processor can drop late results from a batch that has already been
finalized (timeout, replacement, or natural completion). Results that
arrive *after* the active demonstration has changed are discarded — the
visitor moved on and stale outcomes shouldn't drive a fresh inference.

The dispatcher does **not** send tool-call messages to the widget — the
processor sends a single atomic ``tool_call_batch`` server message
*before* calling :meth:`dispatch_batch`, so the widget receives the
whole batch as one unit (no half-sent batches if the bot crashes
mid-loop). The dispatcher just registers per-call futures, awaits the
widget's ``tool_result`` callbacks, and routes outcomes via
``on_outcome`` (which carries ``batch_id`` so the processor can apply
its staleness checks).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Awaitable, Callable

from loguru import logger


class ToolDispatchOutcome:
    """Discriminator for the dispatcher → processor callback."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Callback shape: ``(demo_id, batch_id, call_id, tool_name, args, outcome, payload) → coroutine``.
# ``payload`` is the raw widget result on SUCCESS, an error message string
# on FAILED / TIMEOUT, and ``None`` on CANCELLED.
ToolCallback = Callable[[str, str, str, str, dict, str, object], Awaitable[None]]


class ToolDispatcher:
    """Owns the set of in-flight tool tasks per demonstration.

    Lifetime is tied to the agent processor; ``shutdown()`` cancels
    everything in flight without waiting for individual results.
    """

    def __init__(
        self,
        *,
        on_outcome: ToolCallback,
    ):
        self._on_outcome = on_outcome

        # demo_id → {call_id → asyncio.Task}
        self._tasks: dict[str, dict[str, asyncio.Task]] = defaultdict(dict)
        # call_id → asyncio.Future (resolved by the widget's tool_result message)
        self._pending: dict[str, asyncio.Future] = {}
        # call_id → batch_id (so we can fan outcomes back with the right
        # batch tag without threading it through every method).
        self._call_batch: dict[str, str] = {}
        self._tool_call_count = 0
        self._stopped = False

    # ------------------------------------------------------------------
    # Demonstration / batch lifecycle.
    # ------------------------------------------------------------------

    @staticmethod
    def new_demonstration_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def new_batch_id() -> str:
        return str(uuid.uuid4())

    async def dispatch_batch(
        self,
        *,
        demo_id: str,
        batch_id: str,
        tool_calls: list[dict],
    ) -> None:
        """Register per-call futures + spawn one background task per tool
        call. The widget-side ``tool_call_batch`` server message is sent
        by the processor BEFORE this method is called — this method
        only sets up the receive-side machinery and waits for the
        widget's per-tool ``tool_result`` callbacks.

        ``tool_calls`` items are the dicts the streaming assembler produces:
        ``{"id": ..., "name": ..., "arguments": <json string>}``.
        """
        if self._stopped:
            return
        for tc in tool_calls:
            call_id = tc.get("id") or str(uuid.uuid4())
            name = tc["name"]
            args_raw = tc.get("arguments") or "{}"
            try:
                import json as _json
                args = _json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except Exception:
                args = {}
            self._tool_call_count += 1
            self._call_batch[call_id] = batch_id
            task = asyncio.get_running_loop().create_task(
                self._run_one(demo_id, batch_id, call_id, name, args),
                name=f"in-app-tool:{name}:{call_id[:8]}",
            )
            self._tasks[demo_id][call_id] = task

    async def cancel_demonstration(self, demo_id: str) -> None:
        """Cancel every in-flight tool task for ``demo_id``. Pending
        widget results for those calls are dropped (the futures get a
        CANCELLED outcome, which the processor then ignores when purging
        stale queue items)."""
        bucket = self._tasks.pop(demo_id, None)
        if not bucket:
            return
        for call_id, task in bucket.items():
            if not task.done():
                task.cancel()
            self._pending.pop(call_id, None)
            self._call_batch.pop(call_id, None)
        # Wait briefly for cancellations to settle so we don't leak Tasks.
        if bucket:
            await asyncio.gather(*bucket.values(), return_exceptions=True)

    # ------------------------------------------------------------------
    # Widget tool_result hook — called from the processor's RTVI handler.
    # ------------------------------------------------------------------

    def deliver_widget_result(self, *, call_id: str, data: dict) -> None:
        future = self._pending.pop(call_id, None)
        if future is None or future.done():
            logger.debug(
                f"[in-app-tool] late tool_result for call_id={call_id} dropped"
            )
            return
        future.set_result(data)

    # ------------------------------------------------------------------
    # Single in-flight task.
    # ------------------------------------------------------------------

    async def _run_one(
        self,
        demo_id: str,
        batch_id: str,
        call_id: str,
        name: str,
        args: dict,
    ) -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[call_id] = future
        outcome: str = ToolDispatchOutcome.FAILED
        payload: object = None
        try:
            # No per-tool deadline here — the processor's batch-timeout
            # handler is the single source of truth for "this took too
            # long". When that fires (default 60s, configurable via
            # ``batch_timeout_seconds`` in ``aelios-spark.config.yaml``), it
            # cancels this task via ``cancel_demonstration``, which
            # raises CancelledError below.
            data = await future

            if isinstance(data, dict) and data.get("error"):
                payload = data["error"]
                outcome = ToolDispatchOutcome.FAILED
            else:
                # The widget sends ``{"result": ...}`` on success.
                payload = (
                    data.get("result") if isinstance(data, dict) else data
                )
                outcome = ToolDispatchOutcome.SUCCESS
        except asyncio.CancelledError:
            outcome = ToolDispatchOutcome.CANCELLED
            payload = None
            raise
        finally:
            # Clean up registry — but not if we're already cancelled and
            # ``cancel_demonstration`` already removed us.
            self._pending.pop(call_id, None)
            self._call_batch.pop(call_id, None)
            bucket = self._tasks.get(demo_id)
            if bucket is not None:
                bucket.pop(call_id, None)
                if not bucket:
                    self._tasks.pop(demo_id, None)
            if outcome != ToolDispatchOutcome.CANCELLED:
                # Don't fire a callback for cancellations — the processor
                # already knows the demonstration was discarded.
                try:
                    await self._on_outcome(
                        demo_id, batch_id, call_id, name, args, outcome, payload
                    )
                except Exception as cb_err:
                    logger.error(
                        f"[in-app-tool] outcome callback raised: {cb_err}"
                    )

    # ------------------------------------------------------------------
    # Stats / shutdown.
    # ------------------------------------------------------------------

    @property
    def tool_call_count(self) -> int:
        return self._tool_call_count

    def get_active_demonstration_ids(self) -> list[str]:
        return [d for d, bucket in self._tasks.items() if bucket]

    async def shutdown(self) -> None:
        self._stopped = True
        for demo_id in list(self._tasks.keys()):
            await self.cancel_demonstration(demo_id)
