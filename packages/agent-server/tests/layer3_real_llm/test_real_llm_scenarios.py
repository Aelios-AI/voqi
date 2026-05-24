"""Layer 3: real-LLM scenarios graded by an LLM judge.

These tests:
  1. Spin up a ProcessorHarness with a REAL ChatOpenAI (gpt-5.4 by
     default) instead of the scripted fake.
  2. Drive a multi-turn dialogue per the rubric file.
  3. Capture every (state_context, user_input, agent_output) tuple.
  4. Submit the transcript + master prompt + scenario rubric to a
     separate judge LLM (Claude by default — different family from the
     agent's OpenAI model so it can grade independently).
  5. Assert ``verdict.passed``.

Execution model
---------------
Each ``ProcessorHarness`` is fully self-contained, so we run scenarios
concurrently in batches via ``asyncio.gather``. Default chunk size is 10
(override with ``LAYER3_PARALLELISM``); set
``LAYER3_SCENARIOS=name1,name2`` to filter to a subset for debugging.

Run with:  pytest tests/layer3_real_llm -m llm_judge

Skipped by default. Requires OPENAI_API_KEY (and ANTHROPIC_API_KEY for
Claude judging — falls back to gpt-5.4 as judge if not present)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from brain.config import InAppTool
from tests.harness.llm_judge import JudgeVerdict, judge
from tests.harness.processor_harness import ProcessorHarness, make_runtime_config

pytestmark = pytest.mark.llm_judge

REAL_AGENT_MODEL = os.getenv("IN_APP_LLM_MODEL_FOR_TESTS", "gpt-5.4")

RUBRIC_FILE = Path(__file__).parent / "rubrics.yaml"

DEFAULT_PARALLELISM = 10


def _load_scenarios() -> list[dict]:
    with RUBRIC_FILE.open() as f:
        data = yaml.safe_load(f)
    return data["scenarios"]


def _make_tools(spec: list[dict]) -> list[InAppTool]:
    tools: list[InAppTool] = []
    for t in spec or []:
        tools.append(
            InAppTool(
                name=t["name"],
                description=t.get("description", ""),
                parameters=t.get("parameters") or {"type": "object", "properties": {}},
                requires_confirmation=bool(t.get("requires_confirmation", False)),
            )
        )
    return tools


class _RealLLMHarness(ProcessorHarness):
    """Harness variant that keeps the REAL ChatOpenAI in place and
    captures every round's (state_context, agent_output) tuple."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Restore the real LangChain ChatOpenAI.
        from langchain_openai import ChatOpenAI

        self.processor._llm = ChatOpenAI(  # type: ignore[attr-defined]
            model=REAL_AGENT_MODEL,
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0.6,
        )
        self.rounds: list[dict] = []
        orig_run_round = self.processor._run_one_round
        orig_round_streaming = self.processor._llm_round_streaming

        last_input: dict[str, Any] = {}

        async def capturing_round_streaming(*, wake_mode, batch_state, wake_frame=None):
            state_ctx = self.processor._build_state_context_message(wake_mode=wake_mode)
            output = await orig_round_streaming(
                wake_mode=wake_mode, batch_state=batch_state, wake_frame=wake_frame,
            )
            self.rounds.append(
                {
                    "wake_mode": wake_mode,
                    "batch_state": batch_state,
                    "state_context": state_ctx,
                    "user_input": last_input.get("text", ""),
                    "output": json.loads(output.model_dump_json(exclude_unset=True)),
                }
            )
            return output

        async def capturing_run_round(*, wake_mode, wake_frame=None):
            last_input["text"] = (
                wake_frame.message if wake_frame is not None else "(synthetic wake)"
            )
            return await orig_run_round(wake_mode=wake_mode, wake_frame=wake_frame)

        self.processor._llm_round_streaming = capturing_round_streaming  # type: ignore[assignment]
        self.processor._run_one_round = capturing_run_round  # type: ignore[assignment]


async def _drive_scenario(scenario: dict) -> _RealLLMHarness:
    cfg = make_runtime_config(
        tools=_make_tools(scenario.get("tools", [])),
        software_name=scenario.get("software_name", "TestApp"),
        software_docs=scenario.get("software_docs"),
        software_tldr=scenario.get(
            "software_tldr", "TestApp helps you manage tasks."
        ),
        additional_instructions=scenario.get("additional_instructions"),
        ai_profile=scenario.get("ai_profile"),
        # Guide-mode scenarios opt-in via ``mode: guide`` in their
        # rubric block; default is action mode (matches the bulk of
        # the existing scenarios).
        mode=scenario.get("mode", "action"),
        language=scenario.get("language_code", "en"),
    )
    h = _RealLLMHarness(
        runtime_config=cfg,
        output_language=scenario.get("output_language", "English"),
        language_code=scenario.get("language_code", "en"),
    )
    if "max_batches_per_demo" in scenario:
        h.set_max_batches_per_demo(int(scenario["max_batches_per_demo"]))
    if "history_window" in scenario:
        hw = scenario["history_window"]
        h.set_history_window(
            max_window=int(hw["max_window"]),
            summarize_chunk=int(hw["summarize_chunk"]),
        )
    if "batch_timeout_seconds" in scenario:
        h.set_timeouts(batch_seconds=float(scenario["batch_timeout_seconds"]))
    if "fixed_summary" in scenario:
        # Pre-seed a deterministic summary directly on the history so
        # the rendered prompt's CONVERSATION SO FAR section includes
        # it on the very next render. We don't drive enough turns to
        # naturally trigger summarization in a single Layer 3 scenario.
        h.processor._history._summary = str(scenario["fixed_summary"])
    # Mode-specific knobs. Currently only ``screenshot_force_fail`` —
    # used by guide-mode scenarios that need to exercise the
    # capture-failed apology path. Forces the FakeScreenshotService
    # to return None for the whole scenario.
    extras = scenario.get("mode_extras") or {}
    if extras.get("screenshot_force_fail"):
        h.screenshots.set_default_to_none()

    for turn in scenario["turns"]:
        kind = turn["kind"]
        if kind == "kickoff":
            await h.send_kickoff()
        elif kind == "user":
            await h.send_user(turn["text"])
        elif kind == "text_message":
            await h.send_text_message(turn["text"])
        elif kind == "tool_result":
            target_name = turn.get("last_call_name")
            cid = (
                h.last_call_id(name=None if target_name == "any" else target_name)
            )
            if cid is None:
                # The agent didn't dispatch the tool the rubric was
                # going to feed a result for. Record a synthetic round
                # so the judge sees what happened and stop driving the
                # rubric — the judge will grade the partial transcript.
                h.rounds.append(
                    {
                        "wake_mode": "(rubric_aborted)",
                        "batch_state": "(none)",
                        "state_context": (
                            f"Rubric expected the agent to have dispatched a "
                            f"tool named {target_name!r} by this point, but "
                            f"no matching call_id was found in the "
                            f"dispatch log. Scenario halted."
                        ),
                        "user_input": "(rubric step skipped — agent failed to dispatch expected tool)",
                        "output": {},
                    }
                )
                break
            if "error" in turn:
                await h.deliver_tool_result(call_id=cid, error=turn["error"])
            else:
                await h.deliver_tool_result(call_id=cid, result=turn.get("result"))
        elif kind == "wait_idle":
            await h.pump_until_idle()
        elif kind == "wait_seconds":
            # Used to let real per-instance timeouts (batch, response)
            # fire mid-scenario.
            await asyncio.sleep(float(turn["seconds"]))
            await h.pump_until_idle()
        else:  # pragma: no cover
            raise ValueError(f"unknown turn kind: {kind!r}")
    return h


async def _run_one_scenario(scenario: dict) -> JudgeVerdict:
    """Drive a scenario end-to-end, then have the judge grade it.

    Wraps any exception (LLM provider error, harness timeout, etc.) into
    a failing JudgeVerdict so one broken scenario in a batch doesn't
    take down the rest of ``asyncio.gather``."""
    h: _RealLLMHarness | None = None
    try:
        h = await _drive_scenario(scenario)
        return await judge(
            master_prompt=h.config.build_system_message(
                output_language=h.processor._output_language
            ),
            scenario_name=scenario["name"],
            rubric=scenario["rubric"],
            turns=h.rounds,
        )
    except Exception as e:  # pragma: no cover
        return JudgeVerdict(
            passed=False,
            reason=f"Scenario raised {type(e).__name__}: {e}",
        )
    finally:
        if h is not None:
            try:
                await h.shutdown()
            except Exception:  # pragma: no cover
                pass


def _filter_scenarios(scenarios: list[dict]) -> list[dict]:
    """Apply LAYER3_SCENARIOS env-var filter (comma-separated names)."""
    raw = os.getenv("LAYER3_SCENARIOS", "").strip()
    if not raw:
        return scenarios
    wanted = {s.strip() for s in raw.split(",") if s.strip()}
    out = [s for s in scenarios if s["name"] in wanted]
    missing = wanted - {s["name"] for s in out}
    assert not missing, (
        f"LAYER3_SCENARIOS named scenarios not found in rubrics.yaml: "
        f"{sorted(missing)}"
    )
    return out


async def test_real_llm_all_scenarios():
    """Run every scenario in rubrics.yaml. Scenarios run concurrently
    in chunks of LAYER3_PARALLELISM (default 10) — each scenario builds
    its own ProcessorHarness, so they're fully independent. We
    intentionally do NOT use pytest.mark.parametrize here because that
    forces sequential execution; one batched test running gathered
    coroutines is ~10× faster.

    To debug a single scenario:
        LAYER3_SCENARIOS=confirmation_happy_path pytest tests/layer3_real_llm
    """
    if not os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "").startswith(
        "sk-test"
    ):
        pytest.skip("OPENAI_API_KEY not set; skipping real-LLM scenarios")

    scenarios = _filter_scenarios(_load_scenarios())
    parallelism = int(os.getenv("LAYER3_PARALLELISM", str(DEFAULT_PARALLELISM)))
    parallelism = max(1, parallelism)

    print(
        f"\n[layer3] running {len(scenarios)} scenarios in chunks of {parallelism}"
    )

    results: list[tuple[str, JudgeVerdict]] = []
    for i in range(0, len(scenarios), parallelism):
        chunk = scenarios[i : i + parallelism]
        chunk_names = [s["name"] for s in chunk]
        print(
            f"[layer3] chunk {i // parallelism + 1}: "
            f"dispatching {len(chunk)} scenarios → {chunk_names}"
        )
        chunk_verdicts = await asyncio.gather(
            *(_run_one_scenario(s) for s in chunk)
        )
        for name, verdict in zip(chunk_names, chunk_verdicts):
            status = "PASS" if verdict.passed else "FAIL"
            short_reason = verdict.reason.replace("\n", " ")[:160]
            print(f"[layer3]   {status:4}  {name}  — {short_reason}")
            results.append((name, verdict))

    failures = [(n, v) for n, v in results if not v.passed]
    summary_lines = [
        f"  {'PASS' if v.passed else 'FAIL'}: {n}"
        + ("" if v.passed else f"\n        {v.reason}")
        for n, v in results
    ]
    summary = "\n".join(summary_lines)
    print(
        f"\n[layer3] === FINAL: {len(results) - len(failures)}/{len(results)} passed ===\n"
        f"{summary}"
    )
    assert not failures, (
        f"\n{len(failures)}/{len(results)} Layer 3 scenarios failed:\n{summary}"
    )
