# Voqi agent-server tests

Three layers, in increasing fidelity. Each layer exists for a different
class of bug — running them all on every change is overkill, but the
default `pytest` invocation runs layers 1 & 2 (fast, free, deterministic)
and skips layer 3 (slow, billed). When you change agent behaviour,
work outwards from the layer that's closest to the change.

```
tests/
├── conftest.py              shared fixtures + module-level constant restore
├── harness/                 in-process test plumbing (no network)
│   ├── fakes.py             fake RTVI, aggregators, frame processors
│   ├── processor_harness.py drives InAppAgentProcessor end-to-end offline
│   └── llm_judge.py         layer-3 only: separate LLM grades agent output
├── layer1_unit/             pure functions, schemas, templates
├── layer2_processor/        the full state machine, in-process, scripted LLM
└── layer3_real_llm/         actual OpenAI calls + LLM-as-judge
```

## Running

```bash
uv run pytest                # layers 1 + 2 — fast, free, deterministic (default)
uv run pytest -m llm_judge   # layer 3 only — hits OpenAI ($$$, ~minutes)
uv run pytest -m ""          # all three layers
```

Layer 3 is **deselected by default** via `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "-m 'not llm_judge'"
```

If `OPENAI_API_KEY` is unset (or starts with the test stub
`sk-test-…`), layer 3 self-skips even when explicitly selected — see
`tests/layer3_real_llm/test_real_llm_scenarios.py` for the guard.

## Layer 1 — unit tests

**What it covers**: pure functions and data-shaped contracts where a
processor isn't needed.

| File | What it locks down |
|---|---|
| `test_prompt_rendering.py` | The Jinja system-prompt template — sections, persona, software docs, tools list, conditional gating |
| `test_schema.py` | The Pydantic structured-output schema (`InAppAgentOutput`) per wake mode — which fields are present, which are forbidden, defaults |
| `test_schema_guide_mode.py` | Same as above for `guide` mode — tool_invocations dropped, point_to allowed |
| `test_canned_speech.py` | Canned-speech key resolution + Jinja interpolation with session context |
| `test_dispatcher.py` | `ToolDispatcher` — pending-batch tracking, timeouts, late-result handling, demo isolation |
| `test_history.py` | `InAppConversationHistory` — bounded buffer, summarisation trigger, summary token shape |
| `test_screenshot_service.py` | `ScreenshotService` request/response pairing, timeout handling, oversized payload rejection |
| `test_priority_queue.py` | The internal priority queue (USER_MESSAGE vs SYSTEM vs TOOL_BATCH_COMPLETED ordering) |
| `test_point_to_model.py` | Guide-mode pointing coordinates — normalisation, clamping, label trimming |

**Why this layer exists**: catches type-level regressions and prompt
drift instantly. If a contributor reorders a Jinja `{% if %}` and the
agent stops including the tools section on action-mode wakes, this
layer catches it without booting the processor.

**~300 tests, runs in < 1 second.**

## Layer 2 — processor tests

**What it covers**: the full state machine inside `InAppAgentProcessor`,
driven from outside by a test harness (`tests/harness/processor_harness.py`)
that scripts the LLM with deterministic dicts instead of calling OpenAI.

The harness builds a real `InAppAgentProcessor` with fake collaborators
(RTVI, aggregators) and:

1. Replaces `processor._llm` with `FakeChatOpenAI` — every inference
   round consumes a scripted dict in the order the test queues them.
2. Patches `processor.summary_chain` so history summarisation is
   deterministic and offline.
3. Overrides `create_task` / `cancel_task` / `push_frame` so tests
   don't need Pipecat's `TaskManager`. Pushed frames land in
   `harness.pushed_frames`; tasks become plain `asyncio.create_task`.
4. Exposes `harness.set_timeouts(batch_seconds=..., heartbeat_seconds=...,
   reply_watchdog_seconds=...)` so tests can shrink the real intervals
   (60s batch, 60s heartbeat, 15s reply watchdog) into sub-second
   values, then drive the loop with brief `await asyncio.sleep(0.01)`
   yields to let background tasks settle — no global time mocking.

Tests then drive the processor via its public-ish entry points
(`_handle_user_input`, `_on_tool_outcome`, the priority-queue pump) and
assert against `rtvi.server_messages`, `pushed_frames`, and processor
state.

| File | Scenario class |
|---|---|
| `test_smoke.py` | Round-trip happy path: user voice → LLM → tool batch → results → reply |
| `test_two_trigger_rule.py` | The "two-trigger" guardrail — tool_invocations must be re-justified after a screenshot wake |
| `test_atomic_batch.py` | One `tool_call_batch` per inference round, never per-tool |
| `test_batch_completed_wake.py` | The wake fired when all tool results land — schema gates, prompt context |
| `test_batches_history.py` | How prior batches show up in the history block of subsequent prompts |
| `test_relevance_gate.py` | The relevance gate that decides whether a user wake actually runs inference |
| `test_in_flight_speech.py` | What happens when the user speaks while the agent is mid-reply |
| `test_interruption.py` | Hard interrupts — explicit user "stop", system cancel |
| `test_pending_confirmation.py` | `requires_confirmation` tool flow — agent asks, user yes/no |
| `test_screenshot_capture.py` | `decision_to_request_screenshot` → ScreenshotService → screenshot_result wake |
| `test_action_backstop.py` | Backstop when the LLM says `start_new` but doesn't pick a name |
| `test_demo_lifecycle.py` | Start/continue/end demonstration transitions |
| `test_resolution_with_action_cascade.py` | `pending_batch_resolution` (accept/replace/keep_waiting) under tool cascades |
| `test_stale_results.py` | Late tool results from a batch that's already been timed-out or replaced |
| `test_should_cancel.py` | Cancellation semantics — when in-flight inference is dropped |
| `test_timeouts_guardrails.py` | The real timeouts and guardrails: per-batch (60s), inference retry (3x), reply watchdog (15s), per-demo batch ceiling (8 batches) |
| `test_idle_timer.py` | Idle warning → idle hangup grace window |
| `test_canned_speech_pump.py` | Canned-speech path under various wake reasons |
| `test_incomplete_prompts.py` | `user_turn_status: incomplete` flow — agent waits for more |
| `test_guide_mode.py` | Guide-mode invariants — no tool_invocations, screenshot every turn, point_to allowed |
| `test_state_context_sections.py` | The dynamic state-context the prompt receives each round |
| `test_prompt_rendering_e2e.py` | End-to-end: the prompt the agent receives in a real wake, full template path |

**Why this layer exists**: most regressions are state-machine bugs
(wrong wake fires, prompt missing a section, schema field
inappropriately present, batch resolved at the wrong moment). Layer 1
can't catch these because they require multiple rounds of interaction.
Layer 3 can catch them but is slow and noisy — the deterministic
scripted LLM lets you assert exact behaviour.

**~130 tests, runs in ~5 seconds.**

### The bar for a layer-2 test

Scripting the LLM is only useful when the assertion proves the
**processor** did the right thing in response. A test that scripts X
and then asserts X landed has only proven that `FakeChatOpenAI` echoes
its input — it doesn't exercise any processor logic.

Two failure modes to avoid:

1. **Round-trip echo.** Scripting `speech: "Sure thing."` and then
   asserting `"Sure thing."` reached the transcript proves nothing.
   The positive-path "happy round" is covered once in `test_smoke.py`
   and in the lifecycle tests; don't re-add it elsewhere.

2. **Empty-input tautology.** Suppression branches (off-topic,
   incomplete, kickoff, end_current with stray tools, two-trigger
   violations) need to be tested by scripting **non-empty** speech +
   tools alongside the gating condition and verifying that the
   processor **dropped** them. Scripting empty speech and asserting
   the history stays empty is tautological — empty in trivially
   produces empty out.

Good examples to imitate:

- `test_pending_continue_with_invocations_and_no_resolution_discards`
  in `test_pending_confirmation.py` — scripts a corrupt LLM output
  combo, asserts the two-trigger guard discards the spurious tools.
- `test_screenshot_result_round_can_dispatch_tools_via_start_new` in
  `test_screenshot_capture.py` — explicit regression guard with a
  comment naming the bug it caught.
- The four `test_guard_*` tests in `test_stale_results.py` — each one
  exercises a distinct branch of `_on_tool_outcome`.

## Layer 3 — real LLM tests

**What it covers**: actual conversations with the OpenAI agent, graded
by a separate "judge" LLM against a rubric.

| File | What it asserts |
|---|---|
| `test_real_llm_scenarios.py` | One parametrised test that fans out across the **49 named scenarios** in [`rubrics.yaml`](layer3_real_llm/rubrics.yaml) — create / update / delete / list tools, ambiguous prompts, interrupts, confirmations, demonstrations, multi-turn arcs. Each scenario carries its own rubric; the judge scores the agent's response against it. |
| `test_screenshot_attention.py` | When the agent requests a screenshot, did it actually use the visual info in its reply (vs ignoring it)? |
| `test_guide_point_to_attention.py` | Guide-mode pointing — does the agent point to the *right* element on the page? |

The judge is a **separate LLM from a different family** (Claude by
default, when `ANTHROPIC_API_KEY` is set; falls back to GPT if not) so
it isn't biased by its own prior choices. It receives:

- The agent's master system prompt
- The scenario's rubric (natural-language pass/fail criteria from
  `rubrics.yaml`)
- A turn-by-turn record: for each round, the `wake_mode`,
  `batch_state`, `user_input`, the per-round state-context block
  (capped at 4000 chars), and the agent's full structured output
  (every Pydantic field the LLM emitted, dumped via
  `model_dump_json(exclude_unset=True)`)
- A long `JUDGE_SYSTEM_PROMPT` codifying the contract — per-wake
  schema gating, two-trigger rule, parallel-batch rule, pending-
  confirmation semantics, refusal style

It returns `{passed: bool, reason: str, per_turn: [...]}`. The test
fails on `passed: false` and prints the reason. See
[`tests/harness/llm_judge.py`](harness/llm_judge.py) for the full
contract prompt.

**Why this layer exists**: catches prompt-quality regressions that
type-level tests will miss. A schema change might keep all layer-2
tests green but make the agent's *answers* worse — only a real model
will tell you.

**Cost**: every scenario hits OpenAI for the agent (multi-turn) plus
one Anthropic call for the judge. ~$0.05-0.20 per scenario depending
on length; the full 49-scenario run is roughly $3-8.

**49 conversation scenarios (one parametrised function) + 2 standalone
attention tests = 3 pytest functions total, opt-in only.** Run before
shipping changes to prompts, schemas, or any logic that affects the
model's input.

## When to add tests at each layer

- **Layer 1**: changed a pure function, a Pydantic model, a Jinja
  template, or a dataclass shape.
- **Layer 2**: changed state-machine behaviour — when a wake fires,
  what schema applies, what frames get pushed, what RTVI messages get
  emitted, what the timeout cascade does.
- **Layer 3**: changed the system prompt, knowledge-base format,
  tools-section template, or anything else the LLM reads. Layer 3 is
  the only place you'll catch "this prompt change broke the agent's
  ability to handle ambiguous deletions."

## Adding a new layer-2 scenario

The `harness` (and its variants `harness_with_tools`, `harness_guide_mode`)
are regular pytest fixtures in [`conftest.py`](conftest.py) — request
the one you need by parameter name on the test function. The only
autouse fixture is `_restore_processor_module_constants`, which snapshots
and restores the timeout/retry knobs between tests.

```python
async def test_my_scenario(harness):
    # Script the LLM output the next inference round will consume
    harness.script_llm_outputs([
        {
            "speech": "Sure, creating that now.",
            "demonstration_action": "continue",
            "demonstration_name": None,
            "tool_invocations": [
                {"name": "create_task", "arguments": {"title": "ship it"}},
            ],
        },
    ])

    await harness.send_user("create a task to ship it")

    # Inspect what the processor did
    last = harness.assistant_speech_history[-1]
    assert "Sure, creating that now." in last["content"]

    batch = harness.last_dispatched_batch()
    assert batch is not None
    assert [tc["name"] for tc in batch["tool_calls"]] == ["create_task"]
```

See `test_pending_confirmation.py` or `test_stale_results.py` for the
strongest patterns — each test scripts a plausible LLM mistake and
asserts the processor's defence. The full harness surface
(`script_llm_outputs`, `send_user`, `send_text_message`,
`send_kickoff`, `deliver_tool_result`, `pump_until_idle`,
`set_timeouts`, the `assert_*` helpers, etc.) is defined in
[`harness/processor_harness.py`](harness/processor_harness.py).

## Module-level constants

The processor reads several knobs at import time (timeouts, retry
limits, model name). The autouse
`_restore_processor_module_constants` fixture in `conftest.py`
snapshots and restores these between tests so per-test overrides via
`harness.set_timeouts(...)` don't leak.

## Layer 3 setup

```bash
export OPENAI_API_KEY=sk-real-...           # required for the agent — NOT the stub
export GOOGLE_API_KEY=...                   # required for conversation-history summarisation
export ANTHROPIC_API_KEY=sk-ant-...         # recommended for the judge (different-family grader)
uv run pytest -m llm_judge
```

Layer 3 self-skips when `OPENAI_API_KEY` is missing or starts with
`sk-test-`. If `ANTHROPIC_API_KEY` is unset, the judge falls back to
GPT — which works but loses the cross-family neutrality, so prefer
running with Anthropic credentials when judging anything subjective.

### Layer 3 environment knobs

- `LAYER3_PARALLELISM` (default `10`) — number of scenarios to run
  concurrently in each `asyncio.gather` chunk.
- `LAYER3_SCENARIOS=name1,name2` — filter to a subset of scenarios from
  `rubrics.yaml` by name. Useful when iterating on one rubric.
- `IN_APP_LLM_MODEL_FOR_TESTS` — override the OpenAI model the agent
  uses for layer 3 (default `gpt-5.4`).
