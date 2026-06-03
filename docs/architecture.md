# Architecture

How the pieces fit together once a session is live. This is the
reference for understanding *why* the agent behaves the way it does —
not for getting it running (see [`quickstart.md`](quickstart.md)).

## The three pieces

```
┌──────────────────────────────┐         ┌──────────────────────────┐
│   BROWSER                    │         │   YOUR MACHINE / VPS     │
│   ┌──────────────────────┐   │ /start  │  ┌────────────────────┐  │
│   │ Aelios Spark widget          │ ─────────────►│ Aelios Spark agent server  │  │
│   │  + your defineTool() │   │ WebRTC  │  │  (FastAPI + bot.py)│  │
│   │  + AeliosSpark.configure()  │ ◄══════════►  │                    │  │
│   └──────────────────────┘   │         │  └─────────┬──────────┘  │
└──────────────────────────────┘         │            │             │
                                         │  ┌─────────▼──────────┐  │
                                         │  │  Pipecat pipeline  │  │
                                         │  │  STT → Processor →  │  │
                                         │  │  TTS → transport.   │  │
                                         │  │  output()           │  │
                                         │  └────────────────────┘  │
                                         └──────────────────────────┘
```

Each session is one WebRTC room, one pipeline, one
`InAppAgentProcessor` instance. No shared state between sessions.

## The pipeline

`bot.py` constructs this pipeline once per session, in this order:

```
transport.input()
    ↓ audio frames in
STT (Deepgram Nova-3 — all 37 languages)
    ↓ TranscriptionFrame
user_aggregator (with VAD + optional turn-detection strategies)
    ↓ user-turn-stopped event
InAppAgentProcessor    ←── the brain. Everything below is documented here.
    ↓ LLMTextFrame (sentence-streamed)
TTS (Cartesia)
    ↓ TTSAudioRawFrame
transport.output()
    ↓ audio frames out
assistant_aggregator
```

The aggregators bracket each turn so the LLM context stays consistent;
the transport is Daily WebRTC; STT/TTS are drop-in Pipecat services
that you can swap.

Out-of-band from the audio path, the processor exchanges JSON messages
with the widget via the **RTVI data channel** (a Pipecat abstraction
over the data lane). That's how tool calls, screenshots, and
guide-mode cursor pointing flow.

## The processor — `InAppAgentProcessor`

Think of the processor as the agent's **brain**. It is the only place
in the whole system where an LLM call happens, and every decision the
agent makes — speak this, call those tools, take a screenshot, end
the demonstration, point at this UI element — comes out of one of
those calls.

Events arrive at the brain from all over the system. A visitor
finishes speaking. A tool reports its result back. A screenshot
arrives. An idle timer fires. Each one needs the brain's attention,
and each one is something the brain has to *respond to*. We call
these events **wakes** — they "wake the agent up" to produce a
response.

The brain isn't multi-track. It deliberately processes one wake at a
time, so the agent never produces two overlapping responses or
contradicts itself. The mental model has four moving parts:

1. A **wake queue** — a priority queue events get pushed into
2. A **pump task** — one async task that drains the queue, one wake
   at a time
3. A **wake handler** — for each wake, builds a prompt + schema and
   calls the LLM
4. A few **side services** — tool dispatcher, screenshot service,
   reply watchdog, idle timer, history buffer

### Wake modes

Five kinds of events can wake the agent. Each kind renders a
different system-prompt + a different output schema — see
[Conditional prompting](#conditional-prompting) below for how that
works.

| Wake mode | What triggers it |
|---|---|
| `kickoff` | Session opens — fired by the agent server right after the visitor connects. |
| `user_voice` | Visitor finished a spoken utterance (user-turn-stopped fired). |
| `user_text` | Visitor sent a typed message via the widget's text input. |
| `tool_batch_completed` | Every tool result for a dispatched batch has landed. |
| `screenshot_result` | A screenshot the agent requested has arrived (or timed out). |

### Conditional prompting

Aelios Spark uses one technique across the whole agent loop: **conditional
prompting**. Both the prompt the LLM reads and the schema it must
answer with are rebuilt every round, conditioned on the current
state. The model never sees rules that don't apply this turn, and
it can never express answers that aren't legal this turn.

Two surfaces, same idea:

- **The Jinja system-prompt template** is full of `{% if %}` blocks
  branching on situational state — whether there's an active
  demonstration, whether a confirmable batch is pending, whether a
  screenshot is attached, whether the idle warning is armed, which
  mode the session is in, which language to respond in, and many
  more. The handful named here are examples, not the full list. The
  net effect: a `kickoff` wake might render a tiny prompt with just
  the persona and a greeting cue, while a `tool_batch_completed`
  wake renders the demonstration history, the batch result, and the
  rules for resolving pending state — but nothing about handling a
  fresh user utterance.

- **The Pydantic response schema** is rebuilt per round by
  `build_in_app_schema(wake_mode, mode, idle_stage_two_armed, ...)`.
  Fields that don't apply this turn are literally absent from the
  class. Examples among many: `tool_invocations` is dropped on a
  `kickoff` wake, `point_to` is added in guide mode and dropped in
  action mode, `pending_batch_resolution` only exists when there's
  actually a pending batch to resolve. The full set of conditionals
  lives in `agent_output.py`.

The two surfaces reinforce each other. Both shrink to exactly what's
relevant *this round* — irrelevant rules aren't rendered into the
prompt at all, and irrelevant fields aren't part of the schema at
all. We never ask the model to filter "what applies right now?" from
a sea of rules; the model sees a focused brief and a focused output
shape. This is much more reliable than dumping every rule into one
giant prompt and asking the model to figure out which ones apply.

### The wake queue and the pump loop

Concretely, the brain's one-wake-at-a-time discipline is enforced by
a single queue and a single task that drains it:

```python
self._wake_queue: asyncio.PriorityQueue[RankedEnvelope] = asyncio.PriorityQueue()
self._pump_task: Optional[asyncio.Task] = None  # runs _run_pump_loop()
```

Anywhere in the system that needs the agent's attention — STT
delivering a transcribed utterance, the tool dispatcher reporting a
batch complete, the screenshot service resolving an image, an idle
timer expiring — pushes a wake onto this queue. The pump task pulls
one at a time:

```python
while True:
    envelope = await self._wake_queue.get()
    await self._run_one_round(envelope)   # full LLM round + side effects
```

`RankedEnvelope` wraps each queued wake with an integer priority and
a sequence number so when multiple wakes arrive close together, we
can decide which gets the brain first:

| Priority | `MessageType` |
|---|---|
| 0 | SYSTEM — canned-speech wakes emitted by the processor itself (idle warning, idle goodbye, 80-min session-cap warning) |
| 1 | USER_MESSAGE / TEXT_MESSAGE / KICKOFF / SCREENSHOT_RESULT — anything attributable to the visitor's turn (spoken voice, typed text, session kickoff, or the screenshot continuation of a deictic ask) |
| 3 | TOOL_BATCH_COMPLETED — every tool in the in-flight batch has reported back |

Lower number = drained first. A SYSTEM wake preempts a user turn; a
user-spoken utterance preempts a freshly-arrived tool batch. The
sequence number is the tiebreaker so first-in wins among same-priority
items. SCREENSHOT_RESULT shares the user-tier priority because it's
the continuation of a deictic user turn (the visitor said *"do that
one"*, the agent requested a screenshot, the screenshot lands → same
turn keeps going).

What if a new wake arrives while the brain is mid-round on a previous
one? It waits in the queue. Sometimes the new wake is important enough
to *preempt* the in-flight round — the user starts speaking while the
agent is mid-reply, for instance. That's interruption, covered next.

### A single round: `_run_one_round`

For each wake the pump pulls, the processor:

1. **Builds the prompt context** — calls
   `runtime_config.session_static_template_context()` (persona,
   software docs, tools, language) and merges with per-round dynamic
   state (active demonstration, pending batch, recent history, whether
   a screenshot is attached).
2. **Renders the Jinja template** — `_render_agent_turn_prompt(...)`
   walks the master template's `{% if %}` blocks against the prompt
   context built in step 1 and emits only the sections that apply
   this round (e.g. the pending-confirmation rules section appears
   only when a confirmable batch is parked; the screenshot-context
   block appears only when an image is attached). The model never
   sees rules that don't apply, which keeps the prompt tight and
   focused. The result is the full system-prompt string.
3. **Selects the right schema** — `build_in_app_schema(wake_mode, ...)`
   returns a Pydantic class with only the legal fields for this wake.
4. **Calls the LLM** — `_llm_round_streaming(...)` does
   `ChatOpenAI.with_structured_output(Schema)` and streams sentence
   tokens through.
5. **Applies the decision** — dispatches tool batches, ends/starts
   demonstrations, emits canned speech, queues a screenshot request.

This is the main inference round — the one that produces the
agent's reply and any tool calls. One other place in the agent
server also calls an LLM, with a narrow, non-conversational job:
[`conversation_history.py`](../packages/agent-server/brain/conversation_history.py)
fires a Gemini summary chain when the message buffer overflows
(condenses old messages into a single system-message summary). It
doesn't produce visitor-facing output — it's an internal helper.

### Idle timer

If the visitor goes quiet for long enough, the session should end —
otherwise we keep a paid bot alive listening to dead air. The idle
timer enforces this in two stages:

1. After `IDLE_WARNING_AFTER_SECONDS = 120s` of no valid input, the
   processor enqueues a SYSTEM wake at priority 0 and the agent
   speaks the `IDLE_WARNING` canned line ("still there?").
2. If still no valid input after another
   `IDLE_END_AFTER_WARNING_SECONDS = 60s`, the agent speaks the
   `SESSION_IDLE_GOODBYE` canned line and fires `session_ending` with reason
   `idle_grace_elapsed`. The widget closes the session.

Any **valid user turn** (text, or voice that the LLM classifies as
relevant — not background noise, not a side conversation) resets
the timer back to stage 1.

The key word is "valid". The timer is **not** reset by mere voice
activity — Pipecat's VAD will detect noise, side-conversation
fragments, even a cough, and trigger interruption. None of those are
the visitor actually talking to the agent. So the timer reset is
gated downstream of the LLM's relevance classification, not on the
raw VAD signal. That distinction matters for the next section.

### Interruption

The agent can be cut off mid-response. Three things trigger it:

- **Voice interruption** — the visitor speaks while the agent is
  replying. Pipecat's VAD + user-turn-start strategy push an
  `InterruptionFrame` downstream; the processor catches it and runs
  the teardown.
- **Text interruption** — the visitor types into the text-input
  fallback while the agent is replying. Same teardown as voice.
- **Reply watchdog firing** — the LLM accepts work but produces no
  output within `REPLY_WATCHDOG_SECONDS = 15s`. The processor
  interrupts itself and queues a canned recovery line.

Note: the **idle warning** and the **session-cap warning** do *not*
interrupt. They enqueue a SYSTEM wake at priority 0; the pump picks
it up after the in-flight round finishes and the agent speaks the
canned line on its next turn. No cutting off.

What the teardown actually does (in `_handle_interruption`):

1. **Cancels the reply watchdog** running for the in-flight round.
2. **Cancels the pump task** mid-flight — the async task running
   `_run_one_round` is killed wherever it was.
3. **Drains the wake queue, keeping only put-back items.** Each
   queued frame carries a `put_back_when_interrupted` flag. Frames
   marked put-back (canned apologies, in-flight tool acknowledgements)
   survive; the rest are dropped as stale.
4. **Re-enqueues the in-flight frame if it was put-back.** If the
   wake that was being processed at the moment of interruption was
   marked put-back, a copy goes back on the queue.
5. **Resets the processing flags** (`_processing_blocked`,
   `_processing_event`).
6. **Emits `assistant_interrupted` RTVI** so the widget can update
   its pill state.

The audio cut itself happens out-of-band: Pipecat's
`InterruptionFrame` propagating through the pipeline causes TTS to
stop generating and the transport to drop queued audio frames — the
processor doesn't drive that, it just receives the same frame and
runs its own teardown alongside.

The teardown deliberately does **not** cancel in-flight tool batches
— tools already dispatched to the widget keep running; the LLM's
next round decides whether to use their results or redirect. It also
does **not** reset the [idle timer](#idle-timer) — interruption fires
on any VAD blip, but only a valid user turn counts as the visitor
actually addressing the agent.

## Tool dispatcher

How Aelios Spark turns "the agent wants to do something in your app" into
actual JavaScript running in the browser. Three concepts stack:

```
demonstration   one user-visible accomplishment ("create high-priority task")
   │             └─ spans multiple turns; the agent may dispatch many batches
   ▼
batch           the tool calls the LLM emits in a single inference round
   │             └─ contains one OR many tool invocations, all sharing a batch_id
   ▼
tool invocation a single call to one of the host page's defineTool(...) registrations
                 └─ has a unique call_id; runs in the browser; returns a result
```

Read top-down: a *demonstration* groups what the agent is currently
working on across turns; a *batch* is what it dispatches in one of
those turns; a *tool invocation* is one call within a batch.

### Demonstration — the user-visible thing the agent is doing

A demonstration is the agent's framing of *one accomplishment*. When
the visitor says *"create a high-priority task to ship the release
notes"*, the agent emits `demonstration_action: "start_new"` with
`demonstration_name: "Create high-priority task"` and starts working.
That demonstration stays "active" across however many turns and
batches it takes — the model keeps emitting
`demonstration_action: "continue"` until the work is done — and ends
when the model emits `demonstration_action: "end_current"`.

The active demonstration is part of the prompt context every turn,
so the model always knows what it's currently working on.

Two guardrails on demonstrations:

- **Hard cap**: a single demonstration cannot dispatch more than
  `max_tool_batches_per_demonstration` batches (built-in default 8).
  If the agent hits that cap, the demonstration is force-ended with
  the canned `BATCH_CEILING_HIT` line to the visitor. Prevents
  runaway loops. Override per deployment via
  `max_tool_batches_per_demonstration:` in `aelios-spark.config.yaml`.
- **Clean boundaries**: starting a new demonstration cancels any
  in-flight batch from the previous one. The agent can't be "doing"
  two things at once.

### Batch — the tool calls in one inference round

A **batch** is the unit Aelios Spark ships tool calls in. When the model
returns `tool_invocations: [...]` on an inference round, every entry
in that list is part of the same batch. They share a `batch_id`; each
has its own `call_id`.

**One batch per inference round, maximum.** The model can either
dispatch a batch or not, but it can never dispatch two batches in
one turn.

**Multiple tool invocations per batch is the interesting case.**
Most tool-using agents call one tool at a time, waiting for each
result. Aelios Spark lets the model dispatch *multiple* calls in one batch
when they're genuinely independent — *"mark EX-12 and EX-14 both
as in-progress"* fires `update_status` on each ticket in parallel,
or *"show me my tasks and my open invoices"* runs `list_tasks` and
`list_invoices` side-by-side. The widget runs every call in parallel
via `Promise.all`, each `execute()` awaited independently. Reduces
perceived latency on multi-step requests.

If step B *depends* on step A's output (e.g. *"create a contact for
Alice then add a note to her profile"* — the note needs the contact
id A just minted), the model is supposed to emit A this turn alone
and queue B on the next inference round once A's result lands.
That's a sequential shape, not a parallel one.

The model decides per turn whether the work is parallel-shaped (one
batch with N tool invocations) or sequential-shaped (N batches of
one invocation each, spread across N turns, peeking at results in
between).

#### While a batch is in flight, the agent cannot dispatch another

Say the agent just dispatched a batch of tool calls — and it is still running in the browser. From that
moment until the batch resolves, the agent has exactly three options:

1. **Wait for the results.** When every call reports back (or the
   per-batch timeout fires — default 60s, configurable via
   `batch_timeout_seconds`), a `TOOL_BATCH_COMPLETED` wake lands. On
   *that* round the agent can dispatch a fresh batch — either to
   continue the same demonstration, or anything else.
2. **End the current demonstration.** On any wake (e.g. the visitor
   said something new), the model can emit
   `demonstration_action: "end_current"`. That clears the in-flight
   batch state — the demonstration is done, no follow-up batch.
3. **Start a new demonstration.** Emitting
   `demonstration_action: "start_new"` cancels the previous
   demonstration's in-flight tool tasks, and the same round can
   dispatch a fresh batch under the new demonstration. (Use this
   when the visitor pivots to a different request mid-action.)

What the agent *cannot* do is fire-and-forget — there's no
"dispatch another batch on top of the in-flight one." Each
demonstration carries at most one open batch at a time.

#### How a batch resolves

1. The dispatcher sends a `tool_call_batch` RTVI message carrying
   the full list of `{call_id, name, args}` plus the shared
   `batch_id`.
2. The widget runs every call in parallel. As each settles, it
   emits a `tool_result` message tagged with the matching `call_id`
   and `batch_id`.
3. The dispatcher tracks which `call_id`s have reported back. The
   batch is **complete** when every call_id has a result. If the
   per-batch timeout (built-in default 60s, override via
   `batch_timeout_seconds:` in `aelios-spark.config.yaml`) elapses with any
   call still unreported, the dispatcher marks stragglers as
   `error: "timeout"` and resolves the batch anyway.
4. On resolution, the dispatcher fires a single
   `TOOL_BATCH_COMPLETED` wake carrying all the aggregated results.
   The LLM's next round decides what to do next.

#### What happens when an in-flight batch is interrupted

If the visitor speaks (or types) while a batch is mid-flight:

- Tools already dispatched to the widget **keep running**. Their
  side effects already happened in your code; Aelios Spark doesn't try to
  roll them back. If you don't want a tool firing unless the
  visitor consents, mark it `requiresConfirmation: true` (see
  below).
- The dispatcher keeps tracking the in-flight batch — `_handle_interruption`
  explicitly does NOT cancel pending tool tasks. The `TOOL_BATCH_COMPLETED`
  wake will still fire when the results come back; whether the LLM
  *uses* those results or pivots to the visitor's new input is its
  decision on the next round.
- If the interruption causes the agent to start a *new*
  demonstration on the next turn, the previous demonstration's
  in-flight tool tasks get cancelled at that point (in
  `_start_new_demonstration`), and late `tool_result` messages from
  the now-dead batch are dropped.

### Tool invocation — a single call

A tool invocation is one `{call_id, name, args}` entry within a
batch. Its `name` must match a tool the host page registered with
`AeliosSpark.defineTool(...)`. Its `args` must conform to that tool's
declared `parameters` JSON Schema (the LLM uses
`with_structured_output` against the schema, so malformed args are
rare).

The widget's `toolRegistry.ts` looks up `name` and `await`s the
registered `execute(args)`. Whatever that function returns becomes
the `result` field of the corresponding `tool_result` message;
whatever it throws becomes the `error` field. Either way, the agent
sees it on the next `TOOL_BATCH_COMPLETED` wake and can speak about
it (*"I created task EX-42"* or *"that didn't work — the email was
malformed"*).

### Confirmation flow

Some tools the agent shouldn't fire without the visitor's explicit
go-ahead — deleting records, sending messages, taking irreversible
actions. Mark those `requiresConfirmation: true` when you register
them with `AeliosSpark.defineTool(...)`. The agent then *proposes* the batch
instead of dispatching it.

Full lifecycle:

1. **The agent decides to call a confirmable tool.** Round 1 ends
   with `tool_invocations` containing the confirmable call and
   `speech` saying something like *"I'm going to delete EX-12 — okay?"*.
2. **The batch is parked, not dispatched.** The processor stashes
   the batch in a `_pending_confirmation_batch` slot and emits an
   `assistant_message` to the widget so the visitor hears the
   proposal. No `tool_call_batch` RTVI message goes out yet — the
   tools haven't started running.
3. **The agent waits.** The pump idles; the next wake will be
   either the visitor's voice (yes / no / "actually, do X instead")
   or text input. Idle timer keeps ticking.
4. **The visitor responds.** Their reply lands as a `user_voice` or
   `user_text` wake. The schema for this wake now includes
   `pending_batch_resolution` (because there's actually pending state
   to resolve — conditional prompting in action). The LLM picks one
   of three values:

   - `"accept"` — visitor said yes. The processor releases the held
     batch to the dispatcher, which finally sends the
     `tool_call_batch` RTVI message. From here it's a normal batch:
     parallel execution in the widget, results come back, regular
     `TOOL_BATCH_COMPLETED` flow.
   - `"replace"` — visitor said no, and wants something different.
     The pending batch is discarded; the LLM emits a *new*
     `tool_invocations` list in the same round, and that becomes
     the next batch (which may itself be confirmable, parking
     again).
   - `"keep_waiting"` — visitor's reply was ambiguous. The pending
     batch stays parked, the agent re-asks (*"sorry, did you want
     me to delete it?"*), and we loop back to step 3.

   There's also another way out that bypasses `pending_batch_resolution`
   entirely: the same wake can emit
   `demonstration_action="end_current"` (visitor changed their mind
   and wants to stop, e.g. *"nah, forget it"*) or
   `demonstration_action="start_new"` (visitor pivoted to a fresh
   ask, e.g. *"actually, just create a new ticket called X"*). Both
   trigger the demo-teardown cascade, which calls
   `_reset_demonstration_state` and drops the parked batch alongside
   cancelling any in-flight tool tasks — so the confirmable batch
   never runs. The `pending_batch_resolution` field is irrelevant in
   this case; the cascade has already cleared the slot.

5. **The pending state is preserved through interruption.**
   `_handle_interruption` doesn't touch `_pending_confirmation_batch`
   — if the visitor's interruption happens *while* the agent is
   asking, the next wake (their actual answer) still finds the
   batch parked and can resolve it normally.


## Guide mode

Demonstrations, batches, tool
invocations, the confirmation flow — assumes **action mode**. Guide mode is the architectural opposite: the agent
narrates and points but cannot execute anything in your app. Useful
for onboarding flows, accessibility, or letting visitors learn the
software by being walked through it.

What changes in guide mode:

- **No tool dispatch.** The schema literally does not include
  `tool_invocations` — `build_in_app_schema(wake_mode, mode="guide", ...)`
  drops the field. The LLM can't emit a batch even if it tries; the
  structured-output parser would reject the response.
- **No demonstrations either.** Without batches there's no
  multi-batch arc to group. `demonstration_action` is still in the
  schema (the LLM still tracks "what am I working on") but no tool
  state hangs off it.
- **Screenshot every turn.** Action mode requests a screenshot only
  when the LLM decides (`decision_to_request_screenshot: true`).
  Guide mode pre-attaches a fresh screenshot on every `user_voice`
  and `user_text` wake — the agent needs the visual to point
  accurately, so the prompt assumes one is always there. The
  `decision_to_request_screenshot` field is dropped from the
  schema (always-on, implicit).
- **The `point_to` field appears.** Guide-mode schema adds
  `point_to: { x, y, label } | null` for normalized cursor coordinates
  (see [`modes.md`](modes.md#what-the-agent-can-do-per-turn)). When
  the LLM emits a non-null `point_to`, the processor sends a
  `guide_cursor` RTVI message to the widget, which renders the
  ghost cursor at `(x * viewport.width, y * viewport.height)`.
- **The Jinja template swaps in.** Guide mode uses
  `IN_APP_AGENT_GUIDE_TURN_TEMPLATE` from `brain/config.py`
  instead of the action-mode `IN_APP_AGENT_TURN_TEMPLATE`. The
  guide template has no demonstration-rules, no tool-section, no
  pending-batch logic; it has narration rules and pointing rules
  instead.

What stays the same: the wake queue + pump task discipline, the
five wake modes, the conditional prompting pattern, idle timer,
interruption, conversation history, all the watchdogs except the
batch-related ones (no batches = nothing to watchdog). Guide mode is
strictly the action-mode architecture with the tool-related plumbing
turned off and pointing turned on.

## Screenshot service

`screenshot_service.py`. The agent requests a screenshot only when it
decides one is needed (`decision_to_request_screenshot: true` on
action-mode wakes; always on guide-mode wakes).

Flow:

1. Agent emits `request_screenshot` RTVI message to widget.
2. Widget's `screenshot.ts` lazy-loads `html2canvas-pro`, rasterises
   the DOM, downscales to max 1280px, encodes JPEG at 0.7 quality.
3. Widget replies with `screenshot_response` containing the base64
   image (or an error payload if capture fails).
4. Service resolves the `asyncio.Future` it was holding; processor
   enqueues a `SCREENSHOT_RESULT` wake with the image attached.

Hard timeout: `SCREENSHOT_TIMEOUT_SECONDS = 2.0`. If the widget
doesn't reply in 2 seconds, the future resolves with an error and
the wake fires anyway (so the LLM can still respond, just without
the visual).

The widget's own `[data-aelios-spark-host]` element is excluded from capture
so the widget never sees itself in the screenshot.

## Conversation history

`conversation_history.py`. Rolling buffer of the last ~30 messages.
When the buffer crosses the threshold, a background `asyncio.Task`
fires a Gemini 2.5 Flash summarization call against the oldest chunk
and replaces those messages with a single `[Earlier conversation
summary]` entry.

## Watchdogs and timers

A handful of independent asyncio tasks (server side) and TS timers
(widget side) watch for things going wrong. Server-side defaults are
tunable via env vars (`IN_APP_*` prefix — see `processor.py`); widget
timers are constants in `Widget.tsx`.

| Watcher | Where | Default | What it does |
|---|---|---|---|
| Reply watchdog | server | 15s | If the LLM round doesn't produce output in 15s, fires a system wake that speaks a canned "still thinking" recovery and re-inferences. |
| Batch timeout | server | 60s (default) | If a dispatched tool batch has any call_id unreported after the timeout, marks stragglers as `error: "timeout"` and resolves the batch. Override per deployment via `batch_timeout_seconds:` in `aelios-spark.config.yaml`. |
| Heartbeat timeout | server | 60s | If the widget stops sending heartbeats for 60s, the server pushes `CancelTaskFrame` upstream and the session terminates. |
| Idle warning | server | 120s | After 120s of no valid user input, speaks the `IDLE_WARNING` canned line. |
| Idle grace | server | 60s after warning | If still no valid input, speaks `SESSION_IDLE_GOODBYE` and fires `session_ending` with reason `idle_grace_elapsed`. |
| Session-cap warning | server | 80 min | Speaks a heads-up that the session is about to end. Does NOT interrupt — it just enqueues a SYSTEM wake. |
| Server-side session backstop | server | 100 min | `MAXIMUM_SESSION_DURATION_MINUTES` — the FastAPI `wait_for` around the bot pipeline. Hard backstop; the widget normally cuts first at 90 min. |
| Widget session hard cap | widget | 90 min | The widget self-terminates 10 min before the server backstop, so the visitor never experiences the backstop in normal operation. |
| Widget connecting timeout | widget | 6 min | If the WebRTC handshake never reaches `agent_ready`, flip pill to `failed`. |
| Inference retry | server | 3 attempts | If the LLM throws or returns malformed structured output, the same round retries up to 3 times. On the (cap+1)-th failure, the agent force-ends the demonstration with a canned apology. |
| Max batches per demonstration | server | 8 (default) | Forces `end_current` once the cap is reached to prevent runaway loops. Override per deployment via `max_tool_batches_per_demonstration:` in `aelios-spark.config.yaml`. |
| Screenshot capture | widget | 2s | Per-capture deadline in `screenshot.ts`. Resolves with an error payload on miss. |
| Screenshot service timeout | server | 2s | The server's matching deadline on the request-response pair. Resolves the agent's awaited future with `None` so the wake still fires. |
| Kickoff timeout | server | 5 min | If the widget never connects after `/start` succeeds, the bot self-kills. |

## Canned speech

`canned_speech.py`. Pre-written multilingual responses for situations
where the LLM shouldn't be in the loop:

- `IDLE_WARNING` — the 120s "are you still there?" check-in.
- `SESSION_IDLE_GOODBYE` — fires when the 60s grace window after the
  idle warning lapses; the bot says goodbye and tears itself down.
- `SESSION_CAP_WARNING` — 80-minute "10 minutes until cap" heads-up
  so the visitor can wrap things up before the 90-min hard close.
- `RESPONSE_TIMEOUT` — recovery line for when the LLM accepted work
  but didn't push a response within `REPLY_WATCHDOG_SECONDS`. Spoken
  directly (no LLM round) so the visitor isn't left in dead air.
- `LLM_GENERIC_ERROR` — apology + invitation to try again when a
  single LLM round fails (network, parse, timeout). Used for both
  non-tool-result inferences and the first 1-2 tool-result retries.
- `INFERENCE_RETRY_EXHAUSTED` — tool-result inference failed N times
  in a row on the same in-flight batch. Spoken right before the
  active demonstration is force-ended to break the loop.
- `BATCH_CEILING_HIT` — the active demonstration has dispatched the
  maximum allowed number of tool batches. Spoken right before the
  demonstration is cleared.

Each key has translations for every language Aelios Spark supports; the
processor picks the right translation per session.

## RTVI custom messages

The data-channel protocol between widget and agent. Names are stable
— both sides match on them. Documented in `adapters/rtvi.py`
and `widget/src/types.ts`.

### Server → widget

| Message | Purpose |
|---|---|
| `agent_ready` | Bot pipeline is fully wired; the widget can flip its pill from "connecting" to "ready" and the visitor can start talking. |
| `user_turn_started` | The agent's STT/aggregator detected the visitor starting to speak. Widget uses this for its pill state ("listening"). |
| `user_turn_ended` | Visitor stopped speaking. For text input, the server fires this manually since text bypasses STT. |
| `user_message` | What the agent *heard* from the visitor — voice transcript or typed message, echoed back so the widget can render it in the transcript log. |
| `assistant_turn_started` | The agent is about to start speaking its reply. |
| `assistant_message` | A chunk of the agent's spoken reply (sentence-streamed); the widget coalesces these into single transcript bubbles. |
| `assistant_turn_ended` | The agent finished its reply naturally. |
| `assistant_interrupted` | The agent was cut off (voice/text/reply-watchdog). Widget uses this to drop any in-progress animation. |
| `tool_call_batch` | Dispatch a batch of tools to be executed in the browser. Carries `batch_id` + list of `{call_id, name, args}`. |
| `request_screenshot` | Ask the widget to rasterize the host DOM and send back a JPEG. |
| `guide_cursor` | Guide-mode pointing hint — normalized `x`, `y`, plus a `label` for the ghost cursor. |
| `session_ending` | Server is about to disconnect gracefully. Only two reasons are ever emitted: `visitor_confirmed_end` (the visitor responded to the idle-warning by confirming they're done) and `idle_grace_elapsed` (60s grace after the idle warning lapsed). The 90-min client cap, 100-min server cap, and bot crashes do **not** go through `session_ending` — those drop WebRTC and the widget falls into the `lost` phase. |
| `heartbeat` | Server-side keepalive ping, fires periodically. |

### Widget → server

Sent via Pipecat's `sendClientRequest(type, data)`.

| Message | Purpose |
|---|---|
| `send-text-message` | Typed message from the visitor (text input fallback). Always interrupts whatever the agent is doing. |
| `tool_result` | Report one tool call's outcome — `{batch_id, call_id, result \| error}`. The widget emits one per call_id in the batch. |
| `screenshot_response` | The captured image (or an error payload) responding to a `request_screenshot`. |
| `heartbeat` | Widget-side keepalive ping. Server's heartbeat-timeout watchdog measures gap between these. |

Heartbeats fire every few seconds in both directions; the
heartbeat-timeout watchdog uses them to detect a stalled connection.

## Where to read next

- [`modes.md`](modes.md) — action vs guide modes, what each one is good for
- [`widget.md`](widget.md) — the widget's connection state machine + UX rules
- [`tools.md`](tools.md) — how to write good tool definitions
- [`configuration.md`](configuration.md) — every knob you can turn
- [`../packages/agent-server/tests/README.md`](../packages/agent-server/tests/README.md)
  — three-layer test architecture
