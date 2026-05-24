"""Shared pytest fixtures and module-level test setup.

The processor module reads several knobs from environment variables at
import time (BATCH_TIMEOUT_SECONDS, INFERENCE_RETRY_LIMIT, …). Tests
that change these MUST restore them between cases — handled here via
the autouse ``_restore_processor_module_constants`` fixture.

We also stub the OPENAI_API_KEY env var so the processor's
``ChatOpenAI(...)`` constructor doesn't bail at import time. The real
LLM is replaced by the harness immediately after construction.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")
os.environ.setdefault("GOOGLE_API_KEY", "google-test-not-real")

from brain import processor as processor_mod


@pytest.fixture(autouse=True)
def _restore_processor_module_constants():
    """Snapshot + restore module-level tunables so timeout/retry tests
    don't bleed across test cases."""
    snapshot = {
        "BATCH_TIMEOUT_SECONDS": processor_mod.BATCH_TIMEOUT_SECONDS,
        "INFERENCE_RETRY_LIMIT": processor_mod.INFERENCE_RETRY_LIMIT,
        "MAX_TOOL_BATCHES_PER_DEMONSTRATION": processor_mod.MAX_TOOL_BATCHES_PER_DEMONSTRATION,
        "HEARTBEAT_TIMEOUT_SECONDS": processor_mod.HEARTBEAT_TIMEOUT_SECONDS,
        "REPLY_WATCHDOG_SECONDS": processor_mod.REPLY_WATCHDOG_SECONDS,
        "OPENAI_MODEL": processor_mod.OPENAI_MODEL,
    }
    yield
    for k, v in snapshot.items():
        setattr(processor_mod, k, v)


@pytest.fixture
async def harness():
    """Default harness with no tools registered."""
    from tests.harness.processor_harness import ProcessorHarness

    h = ProcessorHarness()
    try:
        yield h
    finally:
        await h.shutdown()


@pytest.fixture
async def harness_with_tools():
    """Harness with a small tool catalog covering all relevant flags."""
    from tests.harness.processor_harness import ProcessorHarness, make_runtime_config, tool

    cfg = make_runtime_config(
        tools=[
            tool("get_status", description="read-only check"),
            tool("create_task", description="create a task"),
            tool(
                "delete_task",
                description="delete a task",
                requires_confirmation=True,
            ),
            tool(
                "send_email",
                description="send an email",
                requires_confirmation=True,
            ),
            tool("list_tasks", description="list tasks"),
            tool("log_in", description="log the visitor in"),
        ],
    )
    h = ProcessorHarness(runtime_config=cfg)
    try:
        yield h
    finally:
        await h.shutdown()


@pytest.fixture
async def harness_guide_mode():
    """Harness configured for guide mode — no tools, mode=='guide'.

    Mirrors what the widget sends to bot.py when the visitor picks
    'Guide me' on the modal: tool list is empty (the bot also drops it
    server-side even if the embed shipped some), and ``mode`` flips the
    prompt + schema branch.
    """
    from tests.harness.processor_harness import ProcessorHarness, make_runtime_config

    cfg = make_runtime_config(tools=[], mode="guide")
    h = ProcessorHarness(runtime_config=cfg)
    try:
        yield h
    finally:
        await h.shutdown()
