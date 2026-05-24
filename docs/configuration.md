# Configuration

Voqi has three configuration surfaces. Together they replace what a
managed control plane would normally serve.

| | Where it lives | What you set there |
|---|---|---|
| **Widget config** | host app JS, via `Voqi.configure({...})` | Agent server URL, pill position, theme colors |
| **Tool registrations** | host app JS, via `Voqi.defineTool({...})` | The callable functions the agent can invoke during a session |
| **Agent config** | `packages/agent-server/voqi.config.yaml` | Persona, additional instructions, knowledge base, speech-keyword bias, turn-detection toggle, plus per-deployment runtime knobs (LLM model id, per-batch tool timeout, max tool batches per demonstration) |

The split is intentional. Branding and tool definitions live in your
app's frontend code because they're part of your product surface — the
tools close over your app's state, auth, and DOM. The agent's persona,
knowledge base, and any extra instructions live on the machine running
the agent, because that's where the LLM call actually happens.

## Widget config

```ts
Voqi.configure({
    // Required: where your agent server is running.
    agentUrl: "https://agent.example.com/start",

    branding: {
        position: "bottom-right",         // or "bottom-left"
        themeColors: {                    // overrides the default palette
            primary: "#F4F5F7",
            bg: "#0A0A0A",
            text: "#F4F5F7",
            muted: "#A0A0A0",
            onPrimary: "#0A0A0A",
        },
    },
});
```

That's the full widget-side surface. There is no host-supplied agent
name, avatar, software name, logo, welcome message, or language list —
the widget renders its own minimal chrome and ships a hardcoded
37-language picker (see [`Widget.tsx`](../packages/widget/src/Widget.tsx)).
`configure()` can be called multiple times; later calls shallow-merge.

### Theme colors

`themeColors` writes four CSS custom properties (`--voqi-primary`,
`--voqi-bg`, `--voqi-text`, `--voqi-muted`, `--voqi-on-primary`) inside
the widget's Shadow DOM. Omit to use the default dark palette.

### Languages

The widget hardcodes a 37-language picker — the full intersection of
Deepgram Nova-3 (STT) and Cartesia (TTS) coverage. The visitor picks;
the widget sends the chosen `language` code in the `/start` body.
Deepgram Nova-3 handles STT for all 37 languages directly via the
per-session `language` enum; Cartesia TTS auto-picks a voice per
language (all bundled voices are female — pick a feminine `agent.name`
in `voqi.config.yaml` to match). See
[`adapters/languages.py`](../packages/agent-server/adapters/languages.py)
for the widget-code → Deepgram `Language` enum and Cartesia voice maps,
plus the full alphabetical list.

## Tool registrations — `Voqi.defineTool({...})`

Tools are the functions the agent can call during a session — *create a
task*, *navigate to a screen*, *send a message*. They are **not**
configured in any YAML file. They live in your host app's JS, right
next to the app code they operate on, and get registered with the
widget at load time:

```js
Voqi.defineTool({
    name: "create_task",
    description: "Create a new task. Use when the user says 'add', 'create', or names a new piece of work.",
    parameters: {
        type: "object",
        properties: {
            title: { type: "string" },
            assignee: { type: "string" },
            due: { type: "string", description: "ISO date" },
        },
        required: ["title"],
    },
    execute: async ({ title, assignee, due }) => {
        return await myApi.createTask({ title, assignee, due });
    },
    requiresConfirmation: false,
});
```

Why frontend JS and not a config file:

- `execute` is a real JS function. It closes over your app's auth
  session, your API client, your Redux/Zustand store, your router —
  none of which can be serialized into YAML.
- The agent server never sees `execute`. Only the `name`,
  `description`, and `parameters` (JSON Schema) are forwarded at
  session start so the LLM knows the tool's surface. The call itself
  runs in the browser, in your app's context, with your user's
  credentials.

Tools accumulate in an in-memory registry inside the widget. Define as
many as you want, in any order, before or after `Voqi.configure(...)`.
The registry is snapshotted and sent to the agent server when the
visitor clicks Start.

Full tool-writing guide — when to use `requiresConfirmation`, how to
shape return values, parallel batches, error handling, common patterns
— in [`tools.md`](tools.md).

## Agent config — `voqi.config.yaml`

```yaml
agent:
  name: "Acme Assistant"
  personality: "Friendly, precise, asks one clarifying question when ambiguous."

software:
  name: "Acme CRM"
  tldr: "A simple CRM for small teams. Tracks contacts, deals, and notes."
  docs_file: "./knowledge.md"      # OR inline:
  # docs: |
  #   ## Acme CRM
  #   Contacts have name, email, company, and tags...

additional_instructions: |
  You operate Acme CRM on behalf of the user via voice.
  - Be concise. Two sentences max unless asked for detail.
  - Confirm destructive actions (delete, send) before doing them.
  - If you're unsure, ask one clarifying question rather than guessing.

# NOTE: language and mode are NOT configured here. The visitor picks
# both at session start in the widget; the widget always sends them in
# the /start body. If either is missing, the agent server raises.

# End-of-turn detection. Default false = VAD-only (silence ends the
# turn). Set true to run Pipecat's local SmartTurn audio analyzer for
# more natural pauses and better mid-utterance hesitation handling.
# Auto-falls-back to VAD-only for Bulgarian, Croatian, Czech, Greek,
# Gujarati, Hebrew, Hungarian, Kannada, Malay, Romanian, Slovak,
# Swedish, Tagalog, Tamil, Telugu, and Thai — SmartTurn has no model
# for those languages.
turn_detection: false       # true or false

speech_keywords:           # optional — biases STT recognition
  - "Acme"
  - "Q2 OKR"

# Optional — per-demonstration ceiling on dispatched tool batches.
# Acts as a runaway-loop guardrail: if the LLM tries to dispatch a
# batch that would push the demo over this number, the processor
# force-ends the demonstration with a canned apology. Built-in
# default is 8; raise for apps that legitimately need long multi-
# step demos, lower for high-traffic deployments where you want a
# tight per-demo cost cap.
max_tool_batches_per_demonstration: 8

# Optional — seconds the processor waits for every tool in a
# dispatched batch to report back before force-resolving the batch
# as a timeout. Built-in default is 60.0; raise if your tools
# legitimately need longer than a minute, lower for snappier UX
# when everything you call is fast.
batch_timeout_seconds: 60.0

# Optional — OpenAI model id for the main inference round. Override
# to pin a snapshot or swap models per deployment. Leave unset to use
# the processor's built-in default.
llm_model: "gpt-5.4"
```

### Swapping STT / TTS providers

Voqi defaults to Deepgram Nova-3 (STT for all 37 widget languages)
and Cartesia (TTS) because those are what we've learned to be the
best. Both are drop-in Pipecat adapters, so swapping is straightforward:
open `packages/agent-server/bot.py`, find the STT or TTS factory line,
and replace the class. Pipecat ships adapters for:

- **STT**: Deepgram, AssemblyAI, Azure, Google, Whisper, Gladia, Riva,
  Speechmatics, …
- **TTS**: Cartesia, ElevenLabs, OpenAI, Azure, PlayHT, Resemble,
  Rime, LMNT, …

See the [Pipecat services
docs](https://docs.pipecat.ai/) for the full catalogue
and per-adapter env-var setup.

### Knowledge base

The `software.docs` field (or `docs_file` path) is included in every
agent turn. Put whatever information about your software you want the
agent to know — features, behaviour, anything a visitor might ask
about. Plain markdown or prose, whatever reads naturally.

Keep it under roughly **20K tokens** (~80KB of English). It's sent on
every turn, so longer KBs inflate per-turn cost and latency. If your
KB doesn't fit, you need a retrieval layer — out of scope for the OSS
for now. The managed [Aelios AI](https://aeliosai.com) service handles
retrieval for large KBs.

### Additional instructions

This is **not** the full system prompt — Voqi renders the actual
system prompt from a template (persona + software docs + tools list +
turn-handling rules). Anything you put under `additional_instructions`
is concatenated into that template as one extra block. Use it for:

- Style rules ("never use jargon", "always confirm prices")
- Business constraints ("only edit records owned by the active user")
- Persona reinforcement ("you are a financial assistant — be precise")

You don't need to repeat tool descriptions here; the agent already
sees them on every turn.

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | — | Main LLM |
| `DAILY_API_KEY` | yes | — | WebRTC transport |
| `DEEPGRAM_API_KEY` | yes | — | STT — Nova-3 covers all 37 widget languages |
| `CARTESIA_API_KEY` | yes | — | Agent voice (TTS) |
| `GOOGLE_API_KEY` | yes | — | Gemini for conversation-history summarisation |
| `VOQI_ALLOWED_ORIGINS` | optional | `*` (open) | Comma-separated CORS allowlist for the widget's host domains |
| `VOQI_CONFIG` | optional | `./voqi.config.yaml` | Override config file path |

## Where the data flows

1. Browser loads `voqi-widget.js`.
2. Widget reads `data-agent-url` (or `Voqi.configure({ agentUrl })`).
3. Visitor clicks Start → widget POSTs to `${agentUrl}/start` with the
   registered tools, the chosen language, and the chosen mode.
4. Agent server reads `voqi.config.yaml`, merges with the widget body,
   and boots the Pipecat pipeline.
5. WebRTC voice loop runs. Agent calls tools by sending RTVI messages
   back to the widget; widget runs them via the registry and replies.
6. Visitor clicks End (or session caps out). The bot process exits.

Nothing else makes a network call.
