# Modes — action vs guide

Aelios Spark runs in one of two modes per session. The visitor picks at
session start (via the language/mode picker in the widget UI); the
choice is frozen for that session and shapes both what the agent is
allowed to do and what schema the LLM sees on every turn.

## At a glance

| | `action` | `guide` |
|---|---|---|
| Calls your registered tools | yes | **no** |
| Sees the screen | only when the LLM decides one is needed | every turn |
| Points to UI elements | no | yes (ghost cursor) |
| Best for | *doing things in the app* | *narration, onboarding, accessibility* |
| Schema field `tool_invocations` | present | dropped |
| Schema field `decision_to_request_screenshot` | present | dropped (always true implicitly) |
| Schema field `point_to` | dropped | present |
| `requires_confirmation` flow | active | n/a (no tools) |

## Action mode

The agent operates your software on the visitor's behalf by calling
the tools you registered with `AeliosSpark.defineTool(...)`.

### What the agent can do per turn

On a `user_voice` or `user_text` wake, the LLM returns a Pydantic
object. The fields below are always-or-sometimes present depending
on session state — `build_in_app_schema(...)` in
[`agent_output.py`](../packages/agent-server/brain/agent_output.py)
conditionally includes each:

- `speech` — the agent's reply text (TTS'd + transcribed to widget).
- `demonstration_action` — `"continue"`, `"start_new"`, or
  `"end_current"`. Always present.
- `demonstration_name` — required when `demonstration_action == "start_new"`.
  Present whenever `start_new` is in the action enum (i.e. on user
  wakes; absent on `tool_batch_completed`).
- `is_message_relevant` — `"relevant"` or `"off_topic"`. **Only on
  `user_voice`.** The mic is passive — audio might be a side
  conversation, ambient noise, or an indistinct fragment not aimed
  at the agent. The LLM decides this first; `off_topic` short-circuits
  the rest of the round.
- `user_turn_status` — `"complete"`, `"incomplete_short"`, or
  `"incomplete_long"`. **Only on `user_voice`** (text input is
  explicitly directed so it's always complete). The agent can
  decide the visitor isn't done yet and speak a brief nudge while
  waiting for the rest.
- `decision_to_request_screenshot` — boolean; if `true`, the agent
  pauses and asks the widget for a DOM screenshot before producing
  the actual reply. Present on `user_voice` / `user_text`; absent
  on `screenshot_result` (you can't loop forever).
- `screenshot_request_context` — string or null; **required when
  `decision_to_request_screenshot == true`**. A short note from
  this round to the agent's future self describing what to look at
  when the screenshot arrives.
- `pending_batch_resolution` — `"accept"`, `"replace"`, or
  `"keep_waiting"`. Only present when the session is in
  `pending_confirmation` state (a confirmable batch is parked
  waiting for the visitor's go-ahead).
- `idle_warning_resolution` — `"end_session"` or `"continue_session"`.
  Only present when idle stage-2 is armed (the visitor was just
  asked "are you still there?" and the 60-second grace is ticking).
  The LLM classifies the response as confirm-end vs keep-going.
- `tool_invocations` — a list of `{name, args}` to dispatch. Present
  whenever tools are registered AND the wake is one where dispatch
  is legal (user wake, `tool_batch_completed`, `screenshot_result`,
  or pending-confirmation `replace`).

### Screenshot behaviour in action mode

The agent **does not** see the screen by default. It sees it only
when it explicitly says so via `decision_to_request_screenshot: true`.
That call sequence is:

1. Wake fires (`user_voice`).
2. LLM picks `decision_to_request_screenshot: true` (returning only
   that flag, no speech, no tools).
3. Processor sends `request_screenshot` to widget.
4. Widget rasterises DOM and replies (or times out at 2s).
5. Processor queues a `screenshot_result` wake with the image
   attached.
6. LLM responds again — now with the screen in context — and emits
   the actual reply, tools, etc.

This costs an extra inference + ~200ms screenshot turnaround, so the
prompt is engineered to only request a screenshot when the agent
genuinely needs visual context (a UI question, a layout reference, an
on-screen value to verify).

### Demonstrations

When the agent calls one or more tools, the run is grouped into a
"demonstration" — a named multi-batch sequence representing one
user-visible accomplishment. The agent maintains demonstration state
across turns:

- **start_new** — fresh visitor request, names the demonstration
- **continue** — same demonstration, another batch (max 8 batches)
- **end_current** — demonstration complete

History compaction folds completed demonstrations into one summary
line so the prompt doesn't bloat across long sessions.

### Confirmation flow (requiresConfirmation: true)

Destructive tools (delete, send, charge) should set
`requiresConfirmation: true` in their definition. The agent then
*proposes* the batch instead of executing it:

1. LLM emits the batch + a spoken question — *"I'm going to delete
   EX-12 — confirm?"*
2. Processor parks the batch as `pending_confirmation`. Widget shows
   the agent's proposal but does NOT execute.
3. Visitor responds.
4. Next wake, LLM picks `pending_batch_resolution`:
   - `"accept"` → dispatch the held batch
   - `"replace"` → discard, emit a different batch
   - `"keep_waiting"` → response was ambiguous, ask again

Tools with `requiresConfirmation: false` (the default) dispatch
immediately on the same wake the LLM decided to call them.

### Use action mode when

- The agent's job is to *operate* the software, not just describe it
- You have meaningful tools the visitor would otherwise click through
- Hands-free productivity, voice-driven CRUD, dictation-with-effects

## Guide mode

A read-only "show, don't touch" mode. The agent narrates what's on
screen, answers questions about the UI, and points to elements the
visitor should interact with — but it never fires a tool. Useful for
onboarding flows, accessibility, or letting visitors learn the
software by being walked through it.

### What the agent can do per turn

The schema replaces `tool_invocations` + `decision_to_request_screenshot`
with `point_to`:

```python
{
  "speech": "The save button is in the top right.",
  "point_to": {
    "x": 0.93,      # normalised [0, 1] — fraction of viewport width
    "y": 0.04,      # fraction of viewport height
    "label": "Save"
  },
  "demonstration_action": "continue",
  ...
}
```

The widget receives the `point_to` payload as a `guide_cursor` RTVI
message, then renders a ghost cursor at `(x * viewport.width, y *
viewport.height)` with the label floating beside it. The cursor
auto-fades after a few seconds.

`point_to` is optional — when the agent's answering a pure question
("what does this app do?"), it just emits `speech`. Pointing is for
when the visitor needs visual orientation.

### Screenshot behaviour in guide mode

A screenshot is captured **every turn**. The agent needs the visual
state to point accurately, so the prompt assumes a screenshot is
always attached. The `decision_to_request_screenshot` field is dropped
from the schema (always-on, implicit).

### No tool calls, ever

`tool_invocations` is dropped from the schema in guide mode. The
agent literally cannot dispatch tools — there's no schema field for
it. Visitor requests that imply action ("delete this") get a polite
refusal explaining guide mode is read-only.

### Use guide mode when

- First-time onboarding — guide the visitor through your UI by voice
- Accessibility — visually impaired users get a real-time narrator
- Sales demos — let prospects ask "what does this do?" while you
  drive the app
- Support — talk a user through a UI without touching their session

## Mode picker UI

The widget shows the mode picker at session start, side-by-side with
the language picker. The visitor always picks — there is no
server-side "default" you can set. If your product only makes sense
in one mode, you can hide the picker by limiting the supported set
client-side (roadmap).

## Switching modes mid-session

Not supported. The visitor's mode choice freezes for the session.
Mode-switching mid-conversation would require the agent to discard
its accumulated demonstration state — easier to end the session and
start a new one in the other mode.
