# Quickstart

Aelios Spark has three moving pieces. To get a voice agent talking to your app
you need all three running at the same time. This guide assumes
localhost — production hosting is up to you (or use
the managed [Aelios AI](https://aeliosai.com) service).

## Prerequisites

- **Node 20+** for the widget bundle and your app
- **Python 3.12** + [uv](https://docs.astral.sh/uv/) for the agent
- API keys — all five are required:
  - **OpenAI** — main LLM
  - **Daily** — WebRTC transport
  - **Deepgram** — STT (Nova-3 covers all 37 widget languages
    directly via per-session `language` codes)
  - **Cartesia** — TTS (the agent's voice)
  - **Google AI Studio (Gemini)** — conversation-history summarisation

  Full env-var reference:
  [`packages/agent-server/.env.example`](../packages/agent-server/.env.example).

## 1. Start the agent server

```bash
cd packages/agent-server
cp .env.example .env
# fill in all five: OPENAI_API_KEY, DAILY_API_KEY, DEEPGRAM_API_KEY,
# CARTESIA_API_KEY, GOOGLE_API_KEY

uv sync
uv run python server.py
# 🎙️  Aelios Spark agent server ready!
#    → POST http://0.0.0.0:3002/start to begin a session
```

The agent reads
[`aelios-spark.config.yaml`](../packages/agent-server/aelios-spark.config.yaml) for
the system prompt, persona, and knowledge base. Edit that file to
describe *your* software.

## 2. Build the widget bundle

```bash
cd packages/widget
npm install
npm run build
# → dist/aelios-spark-widget.js  (single IIFE, ~875 KB raw / ~245 KB gzipped)
```

For active development, `npm run dev` rebuilds on save.

## 3. Embed the widget

Easiest path: try the included example to see the full integration.

```bash
cd examples/tracker
npm install
npm run copy-widget    # copies dist/aelios-spark-widget.js → public/
npm run dev            # → http://localhost:5180
```

Open the page, click the launcher pill at the bottom-right, and try:

- *"List all in-progress tasks."*
- *"Create a task to ship the v2 release by Friday, mark it urgent."*
- *"Assign EX-12 to Alice."*

To embed in your own app, copy
[`examples/tracker/src/aelios-spark.ts`](../examples/tracker/src/aelios-spark.ts) and
[`examples/tracker/src/embedAeliosSparkWidget.ts`](../examples/tracker/src/embedAeliosSparkWidget.ts)
as starting points, then replace the tool definitions with your own
functions. See [`tools.md`](tools.md) for the tool-writing guide.

## What "action" vs "guide" mode means

| | action | guide |
|---|---|---|
| Calls your tools | yes | no |
| Sees the screen | only when the agent decides | every turn |
| Points to UI elements | no | yes (ghost cursor) |
| Best for | doing things in the app | onboarding, accessibility, narration |

The visitor picks at session start. Both modes are always offered;
the agent server validates that the widget sent one and the
processor's schema branches on it.

## Troubleshooting

**Widget says "cannot resolve agent URL".** Set `data-agent-url` on
the `<script>` tag or call `AeliosSpark.configure({ agentUrl: "..." })` before
mount.

**Agent connects but doesn't respond.** Check the agent server's
stdout — Aelios Spark logs everything to the terminal you started `bot.py`
in. Most often this is a missing API key (look for 401 from
OpenAI/Deepgram/Cartesia).

**"Microphone permission denied".** The host page must be served over
HTTPS (or `localhost`) for browsers to grant mic access. `file://`
doesn't work.

**Tools registered but the agent doesn't call them.** Tool descriptions
are how the agent decides when to call. Make them explicit:
*"Use this when the user says 'create a task' or names a new item to
track"* beats *"Creates a task"*. See [`tools.md`](tools.md).
