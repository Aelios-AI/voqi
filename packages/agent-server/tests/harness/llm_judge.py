"""LLM judge for Layer 3 real-agent tests.

A non-deterministic LLM application can only be judged reliably by
another LLM. We pass the judge:

* The agent's master system prompt (so it understands the contract).
* The full transcript: each turn's user input, the agent's structured
  output, and the per-round state-context block the agent saw.
* A natural-language rubric describing what the agent SHOULD have done.

Judge returns a structured `{passed: bool, per_turn: [...], reason: str}`.

We use Claude (a different family from the agent's GPT) so the judge
isn't biased by its own prior choices. Falls back to GPT if no Claude
key is configured.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class JudgeVerdict:
    passed: bool
    reason: str
    per_turn: list[dict] = field(default_factory=list)


JUDGE_SYSTEM_PROMPT = """\
You grade whether an in-app in-app agent followed its contract.

The contract:

1. Output is a single structured JSON per turn produced via LangChain's
   ``with_structured_output``. The schema is GATED per-wake — fields
   that don't apply this turn are NOT in the schema, so the agent
   omits them entirely (this is correct behaviour, not a violation).

   Always present: ``speech``. ``demonstration_action`` is present on
   user_voice / user_text / tool_batch_completed wakes BUT NOT on
   ``kickoff`` wakes — kickoff is the bot's session-start greeting
   and exposes ONLY ``speech`` (it's a speech-only short-circuit; no
   demo state to act on, no tools to dispatch). Do NOT flag
   ``demonstration_action`` as missing on kickoff turns — that's
   per-wake gating, not a contract violation.

   The ``system`` wake mode no longer exists — the only thing that
   used to fire it (the idle-warning "still there?" check-in) now
   uses canned speech and bypasses the inference path entirely. So
   in any real Layer-3 transcript, the wake_modes you'll see are
   user_voice / user_text / tool_batch_completed / kickoff /
   screenshot_result. ``screenshot_result`` is a fully legitimate
   wake — it fires after the widget canvases the visitor's page on
   demand and ships back the bytes. Do NOT flag screenshot_result
   as a contract violation; it IS the contract.

   **Two runtime modes**: ``action`` (default) and ``guide``. The mode
   is set per-session at start.

   * **Action mode** is what most rubrics in this file describe — the
     agent has tools, demonstrations, batches, the full state machine.
     screenshot_result wakes here happen ONLY when the agent set
     ``decision_to_request_screenshot=true`` on a previous user wake.

   * **Guide mode** is read-only. There are no tools, no
     demonstrations, no batches. The schema for guide-mode turns is
     a tight ``{speech, point_to}`` plus voice-gates where applicable.
     ``demonstration_action``, ``demonstration_name``,
     ``tool_invocations``, ``pending_batch_resolution``,
     ``decision_to_request_screenshot``, and
     ``screenshot_request_context`` are all OMITTED FROM THE SCHEMA in
     guide mode — their absence in the agent's output is correct, not
     a violation.

     The guide-mode flow is:
       - On every user_voice / user_text wake the processor short-
         circuits straight into a screenshot fetch (no LLM call).
       - The actual LLM inference happens at the resulting
         screenshot_result wake. So in guide-mode transcripts the
         user-input rounds you're grading will be wake_mode=
         screenshot_result with the visitor's utterance reflected in
         the conversation history. There is NO separate user_voice/
         user_text round in guide-mode transcripts — that's expected.

     ``point_to`` (guide-mode only) is the cursor coordinate the
     agent emits when the answer involves "click here". Coordinates
     are normalized to the screenshot the agent saw — x and y are
     fractions in [0, 1]. Either ``point_to: null`` or a populated
     object is valid; the rubric will say which the scenario expects.

   Per-wake conditional fields:
   - ``demonstration_action`` enum varies by wake:
     * user_voice / user_text → continue, start_new, end_current
     * tool_batch_completed   → continue, end_current  (NO start_new)
     * kickoff                → field absent (speech-only schema)
   - ``demonstration_name`` is in the schema ONLY when ``start_new`` is
     in the action enum, i.e. on user wakes. On tool_batch_completed
     and kickoff wakes the field is OMITTED from the schema; the agent
     correctly omits it from the output. Do NOT flag a missing
     demonstration_name on these wakes — that's per-wake gating, not
     a violation.
     When ``demonstration_name`` IS in the schema (user wakes) but the
     agent chose ``demonstration_action='continue'`` or
     ``'end_current'`` rather than ``start_new``, emitting
     ``demonstration_name=null`` is CORRECT — the field is required
     by the schema, but its value is only meaningful under start_new.
     Null on a continue/end_current turn is per-wake gating, not a
     contract violation. Only flag demonstration_name when its
     emitted VALUE is missing/empty AND the agent used start_new.
   - ``tool_invocations`` only when the schema exposes it (two-trigger
     rule + tools registered).
   - ``user_turn_status`` only on voice user wakes.
   - ``pending_batch_resolution`` only when batch state is
     pending_confirmation.

2. **Field naming**: the agent's invocation field is deliberately named
   ``tool_invocations``, NOT ``tool_calls``. We do NOT use OpenAI's
   native function-calling protocol — we drive structured output and
   dispatch the resulting list ourselves. Do not flag the use of
   ``tool_invocations`` as a contract violation; it IS the contract.

3. Two-trigger rule for ``tool_invocations``: only on (a) user-input
   wake with start_new, (b) tool_batch_completed wake, or
   (c) pending_confirmation with replace.

4. Tools in a single batch run IN PARALLEL — never sequential.
   Multi-step workflows must be split across turns.

5. Permission to interrupt: never use start_new on an active demo
   unless the visitor's intent is unambiguous. When ambiguous, ask
   first.

6. Pending-confirmation: only accept / replace / keep_waiting. There
   is NO plain decline; on decline the agent must use ``replace`` OR
   ``demonstration_action='end_current'``. When end_current is paired
   with a pending state, ``pending_batch_resolution='keep_waiting'``
   is the correct no-op (the cancellation cascade drops the batch).

   On ``pending_batch_resolution='accept'`` the PARKED batch
   dispatches automatically — the agent should NOT emit
   ``tool_invocations`` on the accept turn (those would be a NEW
   batch, not the held one). Empty / absent ``tool_invocations`` on
   accept is CORRECT, not a violation. Only ``replace`` requires the
   agent to also emit ``tool_invocations`` (the replacement batch).

7. Out-of-scope refusals must be polite and non-condescending; missing
   features should not use phrasing that frames the agent as "only
   in-app" or "only X" — the agent is a general in-app voice +
   chat companion that can both explain the product and (with tools)
   drive the software on the visitor's behalf, so any "I'm only
   designed for X" refusal is wrong unless the request is genuinely
   unrelated to the product.

8. Every turn's structured output may carry the always-required fields
   even when their values are nulls / empty strings / empty lists.
   That is the schema doing its job, not a violation. Only flag
   violations when an EMITTED VALUE contradicts the contract — e.g.
   ``tool_invocations`` populated on a wake that disallows them, or
   ``demonstration_action='start_new'`` without a demonstration_name.

You will receive a rubric describing the specific test scenario. Apply
both the contract above AND the scenario-specific rubric. If a turn
violates either, mark it failed and explain why. Do NOT flag
schema-shape choices that match the contract above.

Respond with JSON only:
{
  "passed": bool,
  "reason": "one paragraph explaining the verdict",
  "per_turn": [{"turn": int, "passed": bool, "note": "..."}, ...]
}
"""


def _build_messages_payload(
    *, master_prompt: str, scenario_name: str, rubric: str, turns: list[dict]
) -> list[dict]:
    # Per-turn state-context truncation: the rendered state context can
    # grow past 4k chars on multi-batch demos where every prior batch
    # carries its results inline. Truncating too aggressively used to
    # hide details the rubric needed (e.g. "agent should have noticed
    # REL-42 was already completed"); 4000 chars holds 3-4 batches of
    # typical density without blowing the judge's context budget.
    STATE_CONTEXT_CAP = 4000
    rendered_turns = []
    for i, t in enumerate(turns, start=1):
        ctx = t.get("state_context", "(omitted)")
        truncated = (
            ctx[:STATE_CONTEXT_CAP] + "\n…[truncated]"
            if len(ctx) > STATE_CONTEXT_CAP
            else ctx
        )
        rendered_turns.append(
            f"--- Turn {i} ---\n"
            f"Wake mode: {t.get('wake_mode', '(unknown)')}\n"
            f"Batch state at turn start: {t.get('batch_state', '(unknown)')}\n"
            f"User input (or wake reason): {t.get('user_input', '(no user input)')}\n"
            f"State context the agent saw: {truncated}\n"
            f"Agent output: {json.dumps(t.get('output', {}), indent=2)}"
        )
    rendered = "\n\n".join(rendered_turns)
    user_msg = (
        f"# Scenario: {scenario_name}\n\n"
        f"# Scenario rubric\n{rubric}\n\n"
        f"# Master system prompt the agent had\n{master_prompt[:6000]}"
        f"{'\\n\\n[truncated]' if len(master_prompt) > 6000 else ''}\n\n"
        f"# Turn-by-turn record\n{rendered}\n\n"
        "Return JSON only."
    )
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


async def judge_with_claude(
    *,
    master_prompt: str,
    scenario_name: str,
    rubric: str,
    turns: list[dict],
    model: str = "claude-opus-4-5",
) -> JudgeVerdict:
    """Use Anthropic's Claude as the judge. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        return await judge_with_openai(
            master_prompt=master_prompt,
            scenario_name=scenario_name,
            rubric=rubric,
            turns=turns,
        )
    client = anthropic.AsyncAnthropic()
    messages = _build_messages_payload(
        master_prompt=master_prompt,
        scenario_name=scenario_name,
        rubric=rubric,
        turns=turns,
    )
    # Anthropic API takes a separate `system` field.
    sys_msg = messages[0]["content"]
    user_msg = messages[1]["content"]
    resp = await client.messages.create(
        model=model,
        max_tokens=2000,
        system=sys_msg,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    return _parse_verdict(text)


async def judge_with_openai(
    *,
    master_prompt: str,
    scenario_name: str,
    rubric: str,
    turns: list[dict],
    model: str = "gpt-5.4",
) -> JudgeVerdict:
    """Use OpenAI's GPT as the judge."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    messages = _build_messages_payload(
        master_prompt=master_prompt,
        scenario_name=scenario_name,
        rubric=rubric,
        turns=turns,
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content or ""
    return _parse_verdict(text)


def _parse_verdict(text: str) -> JudgeVerdict:
    try:
        # Strip markdown fences if any.
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(line for line in cleaned.splitlines() if not line.startswith("```"))
        data = json.loads(cleaned)
        return JudgeVerdict(
            passed=bool(data.get("passed", False)),
            reason=str(data.get("reason", "")),
            per_turn=list(data.get("per_turn", [])),
        )
    except Exception as e:
        return JudgeVerdict(
            passed=False,
            reason=f"Judge response did not parse as JSON: {e}; raw={text[:500]}",
        )


async def judge(**kwargs: Any) -> JudgeVerdict:
    """Default: try Claude, fall back to GPT-5.4 (the user-specified judge)."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return await judge_with_claude(**kwargs)
    return await judge_with_openai(**kwargs)
