# Contributing to Voqi

Thanks for considering a contribution. Voqi is a real OSS project
backed by a real production agent loop — every change touches code
that runs in real-time voice sessions, so we ask for some discipline.

## Before you start

Read these in order:

1. [`README.md`](README.md) — what Voqi is, how to run it
2. [`docs/architecture.md`](docs/architecture.md) — how the system
   works under the hood
3. [`docs/modes.md`](docs/modes.md) — the two operating modes
4. [`docs/widget.md`](docs/widget.md) — widget lifecycle + UX rules
5. [`packages/agent-server/tests/README.md`](packages/agent-server/tests/README.md)
   — the three-layer test architecture

If you skip these and open a PR, expect the review to bounce you
back to them. The codebase has non-obvious constraints (priority
queue ordering, schema gating per wake, demonstration batch caps)
that aren't visible from the diff alone.

## Dev setup

You need **Node 20+**, **Python 3.12**, and [**uv**](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Aelios-AI/voqi
cd voqi

# Agent server
cd packages/agent-server
uv sync
cp .env.example .env       # fill in API keys for layer-3 tests
uv run pytest -q           # should print "440 passed"

# Widget
cd ../widget
npm install
npm run build              # → dist/voqi-widget.js

# Example app — full end-to-end smoke test
cd ../../examples/tracker
npm install
npm run copy-widget
npm run dev                # → http://localhost:5180
```

The example tracker is the canonical "is everything still working?"
test. If you can launch it, click the pill, and run a couple of voice
commands ("list tasks", "create a task"), the system is healthy.

## What kinds of contributions we want

| Type of change | Welcome | Reviewed carefully |
|---|---|---|
| Bug fixes with a failing test | ✅ | |
| New STT / TTS Pipecat adapters wired in `bot.py` | ✅ | |
| New scenarios added to `tests/layer3_real_llm/rubrics.yaml` (strengthen regression coverage) | ✅ | |
| Docs improvements — typos, accuracy fixes, new examples | ✅ | |
| Mock-mode improvements in `mockTransport.ts` / `mockApi.ts` | ✅ | |
| Example apps in `examples/` (beyond the tracker) | ✅ | |
| Theming presets / colour palettes via the five CSS tokens | ✅ | |
| Performance improvements with reproducible benchmarks | ✅ | |
| New `adapters/` wrappers (STT/TTS/turn-detection/transport glue) | | ✅ |
| Widget UI polish + accessibility (Shadow-DOM contained) | | ✅ |
| Editing `canned_speech.py` (multilingual, user-visible copy) | | ✅ |
| Changes to the 37-language picker or `adapters/languages.py` (touches STT / TTS voice mapping) | | ✅ |
| Changing the five host-overridable CSS tokens or `VoqiUserConfig` surface (public API for embedders) | | ✅ |
| Changes to the structured-output schema (`agent_output.py`) | | ✅ |
| Changes to the master Jinja system-prompt template | | ✅ |
| Changes to the priority queue ordering / wake-mode set / wake priorities | | ✅ |
| Changes to the demonstration lifecycle (batch caps, confirmation flow) | | ✅ |
| Changes to the idle-timer protocol or session-ending reasons | | ✅ |
| Changes to the RTVI custom-message protocol (widget ↔ server contract) | | ✅ |
| Adding new agent capabilities (new wake modes, new tools-like primitives) | | ✅ |

"Reviewed carefully" doesn't mean rejected — it means a maintainer
will read the PR end-to-end and probably ask for a layer-3 run.
These are areas where a seemingly innocuous change can break the
production agent's behaviour in subtle ways, or ripples across the
widget ↔ server contract. Including a short rationale in the PR
description (what failure mode you're fixing, what you considered
and rejected) accelerates the review.

## Code style

### Python

- **Ruff** for linting + imports. `uv run ruff check .` must print
  `All checks passed!` — `master` is green. Configured in
  `pyproject.toml`.
- Line length 100, sorted imports, double quotes preferred.
- **Type hints** on every public function. The processor is a
  state-machine — readers rely on types to navigate it.
- **No `print`** in production paths — use `loguru`. Voqi's default
  sink is stdout (no file-rotating handler); structured logging
  still matters so you can grep + filter.
- **Docstrings** on every non-trivial method, especially in
  `processor.py`. Explain *why* the method exists, not what it does
  (the code already says what).
- **No emojis** in code or commits.

### TypeScript

- TSC strict. Run `npx tsc --noEmit` from `packages/widget` before
  pushing. There is currently **one pre-existing error**: a
  `PipecatClient` type-identity mismatch between
  `@pipecat-ai/client-js` and `@pipecat-ai/client-react` — the type
  is imported from `client-js` and held in widget state, then handed
  to `<PipecatClientProvider client={...}>` from `client-react`, which
  re-declares the same name. Until upstream Pipecat re-exports the
  type from a single module, that error stays. Your PR should not
  introduce additional errors.
- Functional React, no class components.
- All public types exported from `widget/src/types.ts`.
- Comments explain why, not what.

### Naming

- Boring is good. Descriptive names beat clever ones.
- `brain/` is the agent loop — LLM call, tool dispatch, state machine.
  `adapters/` is everything that sits between the brain and the outside
  world (STT, TTS, turn-detection, RTVI). Keep that line sharp.

## Testing requirements

Voqi has three test layers — see
[`packages/agent-server/tests/README.md`](packages/agent-server/tests/README.md)
for the full breakdown. **Every PR must:**

1. Pass **layer 1** + **layer 2** (run by default):
   ```bash
   cd packages/agent-server && uv run pytest
   ```
   `440 passed` is the current bar. You should leave it green.

2. Add tests at the **right layer** for your change:

| You're changing... | Add tests in |
|---|---|
| A pure function, a Pydantic model, a Jinja template, or a class testable in isolation (`ToolDispatcher`, `InAppConversationHistory`, `ScreenshotService`, `RankedEnvelope`, `build_in_app_schema`) | `tests/layer1_unit/` |
| State-machine behaviour — which wake fires, which schema applies, what frames get pushed, what RTVI messages get emitted, demonstration lifecycle (start_new / continue / end_current), tool-result handling (stale-result guards, pending-confirmation resolution, batch-completed reconciliation), interruption, idle timer, screenshot capture, two-trigger rule, the timeout/retry/ceiling guardrails | `tests/layer2_processor/` |
| Anything the real LLM reads or has to interpret — the system-prompt template wording, knowledge-base format, tool descriptions, screenshot attention, guide-mode `point_to` grounding, conversation-quality regressions | `tests/layer3_real_llm/` (opt-in, billed) |

3. **Run layer 3 yourself** when your change could affect what the
   language model reads or has to interpret in the main inference
   round — system-prompt template wording, tool descriptions,
   knowledge-base format, screenshot handling, the guide-mode
   prompt, and also any state-machine change that reshapes the
   per-round state-context block, the conversation-history view,
   or how tool results land in the next prompt. When in doubt, run
   it. The judge LLM grades against rubrics — one stochastic
   failure is normal, two means you need to look carefully. You'll
   need real `OPENAI_API_KEY` and `GOOGLE_API_KEY`:

   ```bash
   cd packages/agent-server
   set -a && . ./.env && set +a
   uv run pytest -m llm_judge
   ```

### Test harnesses

Layer 2 uses `tests/harness/processor_harness.py` — a deterministic
in-process driver that scripts the LLM with canned response dicts.
Read that file before writing layer-2 tests; the patterns there are
the patterns to follow.

Layer 3 uses an LLM-as-judge scoring real conversations against
rubrics defined inline in scenarios. See
`tests/layer3_real_llm/test_real_llm_scenarios.py` for the rubric
DSL.

## Commit messages

Conventional commits, lowercase summary, no emojis:

```
fix: cancel idle timer when system wake preempts a user wake
feat(widget): add data-position attribute for launcher placement
docs: clarify the demonstration cap semantics
test: cover screenshot timeout recovery on action-mode wakes
chore: bump pipecat-ai to 1.7.2
```

For PRs that touch the agent loop, include:

- **What** the change is (one sentence)
- **Why** it's needed (link to the issue or describe the failure mode)
- **Which layer of tests** you added/changed
- **Whether you ran layer 3** and the result

## PR process

1. Fork, branch, push, open PR against `master`.
2. CI runs layers 1 + 2 automatically. Layer 3 is gated behind a
   maintainer trigger (it's billed).
3. A maintainer reviews. Expect 1-2 rounds of review for non-trivial
   changes.
4. Once approved and CI is green, a maintainer squash-merges.

## Questions, discussions, ideas

- **Bug?** Open an issue with a reproducer (the example tracker is
  the easiest place to write one).
- **Design discussion?** Open an issue tagged `discussion` — much
  cheaper than writing the PR and finding out the approach was wrong.
- **Anything else?** Email <victor@aeliosai.com>.

## License

By contributing, you agree your contributions are licensed under
[Apache 2.0](LICENSE), the project's license.
