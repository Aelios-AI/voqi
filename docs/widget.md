# Widget behaviour

Everything the widget does in the browser — how it mounts, what
states it transitions through, how it handles failures, and the
timing rules baked in. Reference for the embeddable JS bundle living
in [`packages/widget`](../packages/widget).

## Bundle anatomy

The widget builds to a single self-contained IIFE
(`packages/widget/dist/aelios-spark-widget.js`). When the host page loads it,
the bundle:

1. Registers `window.AeliosSpark` and `window.AeliosSparkReady` (drainable queue).
2. Finds its own `<script>` tag, reads `data-agent-url` /
   `data-mock` / `data-auto-mount` attributes.
3. Drains any callbacks the host already pushed into
   `window.AeliosSparkReady` (in case the host's setup code ran before the
   bundle finished loading).
4. Auto-mounts on `DOMContentLoaded` unless
   `data-auto-mount="false"`.

The widget renders inside a Shadow DOM rooted on a `<div data-aelios-spark-host>`
appended to `<body>`. Host CSS cannot bleed in; widget CSS cannot bleed
out.

## Registration — two surfaces, one for each concern

The host page interacts with the widget through two registration
patterns. They serve different concerns and can be called in any
order.

### `AeliosSpark.configure({...})` — agent URL + widget look

Static config that points the widget at your agent server and tweaks
how the pill looks:

```js
AeliosSpark.configure({
    agentUrl: "https://agent.example.com/start",
    branding: {
        position: "bottom-right",            // or "bottom-left"
        themeColors: {                       // CSS palette overrides
            primary: "...", bg: "...", text: "...",
            muted: "...", onPrimary: "...",
        },
    },
});
```

That's the whole host-facing surface. No software name, no logo, no
agent name, no avatar, no welcome message, no named-theme catalog, no
host-supplied language list — the widget renders its own minimal
chrome and ships a hardcoded 37-language picker (see
[`Widget.tsx`](../packages/widget/src/Widget.tsx)).

Idempotent and re-callable — later calls shallow-merge into earlier
ones. Read lazily at mount, so order doesn't matter relative to
`defineTool` calls.

### `AeliosSpark.defineTool({...})` — functions the agent can call

The host page's "what can the agent actually do in my app" surface.
Each registered tool becomes a function the LLM may invoke during
voice turns:

```js
AeliosSpark.defineTool({
    name: "create_contact",
    description: "Add a new contact. Use when the user says 'add' or names a new person.",
    parameters: {
        type: "object",
        properties: {
            name: { type: "string" },
            email: { type: "string" },
        },
        required: ["name"],
    },
    execute: async ({ name, email }) => myApi.createContact({ name, email }),
    requiresConfirmation: false,    // true for destructive ops
});
```

Tools accumulate in an in-memory `ToolRegistry`. At session start,
the registry's schema list is forwarded to the agent server in the
`/start` POST body — that's how the LLM learns what tools exist this
session. Adding tools mid-session has no effect; only what's
registered at `/start` time counts.

See [`tools.md`](tools.md) for tool-writing patterns.

### The `AeliosSparkReady` queue — order-independent setup

```js
window.AeliosSparkReady = window.AeliosSparkReady || [];
window.AeliosSparkReady.push((AeliosSpark) => {
    AeliosSpark.configure({ ... });
    AeliosSpark.defineTool({ ... });
    AeliosSpark.defineTool({ ... });
});
```

The queue runs every callback once the bundle loads. Pushing into
it from your host page is safer than calling `window.AeliosSpark.foo()`
directly — works whether your code runs before or after the bundle.

## Connection state machine

The widget tracks a `connectionPhase` for the current open-cycle:

```
fresh ──► connecting ──► ready ──► ended
            │ │             │
            │ │             ├──► lost                  (mid-session disconnect)
            │ │             ├──► permission_denied     (mic blocked)
            │ └──► failed                              (6-min connect watchdog)
            └──► failed                                (no /start response in budget)
```

| Phase | When it's set | What the visitor sees |
|---|---|---|
| `fresh` | Initial state. Pill is collapsed, no session yet. | "Tap to talk" launcher |
| `connecting` | Pressed Start → widget POSTs to `/start`, WebRTC handshake in flight. | "Connecting…" |
| `ready` | Bot pipeline wired, `agent_ready` message received. | Live pill with VAD glyph + transcript |
| `ended` | Visitor pressed End, or session hit the 90-min cap. | Brief "Session ended" hint, then back to launcher |
| `failed` | Connection never reached `ready` — `/start` returned an error, agent never sent `agent_ready` within 6 min, or polling budget exhausted. | "Connection failed" |
| `lost` | Was `ready`, then the WebRTC track unexpectedly dropped. | "Connection lost" |
| `permission_denied` | Browser/OS refused mic access. | "Microphone permission denied" |

The status label is a single short string — there's no inline "try
again" / "restart?" prompt or instructions. The visitor closes the
widget and re-opens the launcher to retry.

Each terminal phase auto-clears its "Session ended" hint after 10
seconds so the widget never sits on a stale banner.

## Session timing rules

All defined as constants near the top of
[`Widget.tsx`](../packages/widget/src/Widget.tsx). Most are not
configurable from outside; they're tuned in code.

### `CONNECTING_TIMEOUT_MS = 6 * 60 * 1000`

If `connectionPhase` is `connecting` for more than 6 minutes — the
agent server's `/start` polling budget is 5 min for cold-start
tolerance, plus a 1-minute buffer for the bot to finish booting after
`/start` returns — the widget flips to `failed`. Sized to never
race a legitimately slow cold start.

### `SESSION_HARD_CAP_MS = 90 * 60 * 1000`

After 90 minutes of an active session, the widget silently closes
with a brief "Session ended" hint. No advance warning UI — the
visitor's last interaction is final. Aelios Spark's server backstop fires at
100 min as a safety net; the 90-min client cap prevents the visitor
from ever seeing it.

### `ENDING_HINT_AUTO_CLEAR_MS = 10_000`

Any `sessionEndingHint` ("Session ended", "Connection lost",
"Microphone blocked") auto-clears in 10s.

### `CAPTION_LINGER_MS = 20_000`

The floating caption above the pill (last two assistant turns when
the popover is closed) lingers for 20s after the assistant turn
ends. While the agent is mid-speech, the captions persist
indefinitely.

### `TRANSCRIPT_HARD_CAP = 200`

The popover transcript caps at 200 raw entries — with the widget's
sentence-coalescing, that's roughly 30-40 distinct exchanges. Older
entries scroll off; the popover scrolls.

## Server-driven session endings

The agent server can announce the end of a session by sending a
`session_ending` RTVI message with a `reason` field. In the OSS, only
two reasons are ever emitted:

| reason | What triggered it | Widget response |
|---|---|---|
| `visitor_confirmed_end` | Visitor told the agent to end (the bot confirms, then closes) | "Session ended." hint, the bot's farewell sentence is allowed to linger for `lingerSeconds`, then the WebRTC connection tears down |
| `idle_grace_elapsed` | 120s of no input → canned idle warning → another 60s of no input → canned goodbye | "Session ended." hint, immediate disconnect |

Anything else that ends a session does **not** go through
`session_ending`:

- **100-min server hard cap.** [`server.py`](../packages/agent-server/server.py)
  wraps the bot task in `asyncio.wait_for(..., timeout=MAXIMUM_SESSION_DURATION_MINUTES * 60)`.
  When it fires, the bot task is cancelled, the WebRTC track drops,
  and the widget sees `onUnexpectedDisconnect` → flips to `lost`
  ("Connection lost"). It's a safety backstop, not a graceful exit —
  the client's 90-min `SESSION_HARD_CAP_MS` is what gives the visitor
  the polite "Session ended" path.
- **Bot crash / network blip.** Same path as above — WebRTC drops,
  widget flips to `lost`.

## Idle protocol

Server-side, in two stages (defaults; both env-configurable):

1. After **120s** with no *valid* user turn, the processor emits a
   SYSTEM wake that speaks the `IDLE_WARNING` canned line — a
   localized check-in along the lines of *"If there's nothing else
   you need help with, I'll close the session in about a minute.
   Just let me know otherwise."* (the full multilingual catalogue
   lives in
   [`canned_speech.py`](../packages/agent-server/brain/canned_speech.py)).
2. The warning opens a **60s** grace window. Three things can happen
   inside it:
   - **Visitor stays engaged** — a valid user turn (text or
     LLM-classified `relevant` voice) cancels the timer and the
     session continues normally.
   - **Visitor confirms they're done** — they reply something like
     *"yes, end it"*. The LLM sets
     `idle_warning_resolution = "end_session"`; the processor fires
     `session_ending` *immediately* with reason
     `visitor_confirmed_end` (no goodbye-line wait).
   - **Visitor goes silent** — the 60s timer elapses. The processor
     speaks `IDLE_GOODBYE` and fires `session_ending` with reason
     `idle_grace_elapsed`.

A "valid user turn" is text (always counts) **or** voice that the
LLM's relevance filter classified as `relevant`. Off-topic chatter
the visitor speaks at someone else in the room does **not** reset
the timer — that's deliberate, because counting raw VAD activity
would mean the agent could never time out in a noisy room. The
gating happens after each round in
[`processor.py`](../packages/agent-server/brain/processor.py)
(`was_valid_user_turn` → `_cancel_idle_timer`).

The widget sees only the canned speech + the eventual
`session_ending` — there's no separate "idle" widget state.

## Permission denial

When the visitor clicks "Block" on the browser's mic prompt (or the
host page's `permissions-policy` denies mic), Daily's track-acquire
call rejects. The widget flips `connectionPhase` to
`permission_denied`, the status label reads
*"Microphone permission denied"*, and every control except End is
disabled.

Recovery is the same as every other trouble state: the visitor
clicks End to tear down, then re-opens the launcher to try again.
The widget cannot observe a browser-settings change, so there is no
auto-retry path — if the visitor wants to grant the permission, they
do it themselves between End and the next Start.

## Mock mode — UI without a backend

```html
<script src="/aelios-spark-widget.js" data-mock="true"></script>
```

Replaces both the API client and the WebRTC transport with
deterministic fakes. No agent server needed; no network calls. A
console control surface lands at `window.__aeliosSparkMock` for driving
widget state from the dev console or test harnesses:

```js
window.__aeliosSparkMock.simulateUserSpeech("hello")
window.__aeliosSparkMock.simulateBotMessage("hi back")
window.__aeliosSparkMock.simulateConnectionLoss()
window.__aeliosSparkMock.simulateGuideCursor(0.5, 0.3, "Click here")
window.__aeliosSparkMock.simulateIdleTimeoutEnd()
window.__aeliosSparkMock.simulatePermissionDenied()
```

Mock mode is the right tool for visually QA'ing widget behaviour
across states without setting up the agent server. See
[`mockApi.ts`](../packages/widget/src/mockApi.ts) and
[`mockTransport.ts`](../packages/widget/src/mockTransport.ts).

## Theming

The widget exposes five CSS custom properties scoped to the Shadow
DOM. Pass `themeColors` to `AeliosSpark.configure({ branding: { themeColors } })`
to override:

```js
themeColors: {
    primary: "#F4F5F7",   // accent — glyph, dots, send button
    bg: "#0A0A0A",        // pill body
    text: "#F4F5F7",      // primary text
    muted: "#A0A0A0",     // status + secondary glyphs
    onPrimary: "#0A0A0A", // text color on primary-coloured surfaces
}
```

That's the whole theming surface. There's no named-theme catalog —
the default dark palette is used if you omit `themeColors`.

## Position

`branding.position` is one of `"bottom-right"` (default) or
`"bottom-left"`. The popover and caption stack expand *upward* from
the pill, so only bottom anchors are supported — anchoring at the
top would push them off-screen.

## Tool execution semantics in the widget

When a `tool_call_batch` RTVI message arrives:

1. Widget iterates the batch's `tool_calls`, looks up each by `name`
   in the `ToolRegistry`.
2. All `execute(args)` invocations run **in parallel** (`Promise.all`).
3. As each one resolves (or throws), the widget sends a single
   `tool_result` message tagged with the matching `call_id` and
   `batch_id`.
4. If a tool's `execute` throws, the result carries an `error: "..."`
   instead of a `result` field; the agent sees the error and can
   recover.
5. If a tool has `requiresConfirmation: true`, the agent verbally
   asks the visitor to confirm before the widget runs `execute`. The
   server parks the batch as pending; `execute` only fires after the
   visitor's voice (or typed) reply is classified as acceptance —
   the confirmation surface is the conversation itself, not a widget
   modal (see
   [`modes.md`](modes.md#confirmation-flow-requiresconfirmation-true)).

The agent server's per-batch timeout (default 60s, configurable via
`batch_timeout_seconds:` in `aelios-spark.config.yaml`) resolves the batch
as failed if any tool takes longer than that to report back.

## Where to read next

- [`architecture.md`](architecture.md) — what the agent server is
  doing on the other side of the WebRTC link
- [`modes.md`](modes.md) — action vs guide mode mechanics
- [`tools.md`](tools.md) — writing good tool definitions
- [`configuration.md`](configuration.md) — every config knob
