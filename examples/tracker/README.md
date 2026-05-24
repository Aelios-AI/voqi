# Example Tracker — Voqi sample integration

A standalone, frontend-only project-management workspace that the Voqi
widget drives end-to-end. Every name, task, and email address is
fictional. Use this as the reference for integrating Voqi into your
own app — the tool definitions in [`src/voqi.ts`](src/voqi.ts) and
the embed script in [`src/embedVoqiWidget.ts`](src/embedVoqiWidget.ts)
are designed to be copy-pasted as a starting point.

## Run it locally

You'll need three things running:

1. The agent server (Python) — see [`packages/agent-server`](../../packages/agent-server)
2. The widget bundle (build once) — see [`packages/widget`](../../packages/widget)
3. This example app

```bash
# 1. start the agent server (from packages/agent-server)
uv run python server.py    # listens on :3002

# 2. build the widget bundle (from packages/widget)
npm install && npm run build

# 3. run the example app (from this directory)
npm install
npm run copy-widget     # copies the built bundle to ./public
npm run dev             # → http://localhost:5180
```

Open `http://localhost:5180`, click the launcher in the bottom-right,
and try voice commands like:

- *"List tasks assigned to Alice."*
- *"Create a task to ship release notes by Friday."*
- *"Move EX-12 to in-progress."*

## What's in here

| Path | What it does |
|---|---|
| [`src/voqi.ts`](src/voqi.ts) | All tool definitions registered with `Voqi.defineTool(...)`. Each tool reads/writes the in-memory Zustand store. Read this to see how a real integration looks. |
| [`src/embedVoqiWidget.ts`](src/embedVoqiWidget.ts) | Injects the widget `<script>` tag at runtime. Points to a local agent URL by default. |
| [`src/store/`](src/store) | Zustand store backing the workspace. Hard reload re-seeds from scratch — no persistence. |
| [`src/api/`](src/api) | `window.example.*` programmatic surface — alternative to voice for E2E tests. |
| [`src/pages/`](src/pages) | The UI itself (board, list, sprint, settings, etc.). |

## How tool registration works

```ts
window.VoqiReady = window.VoqiReady || [];
window.VoqiReady.push((Voqi) => {
    Voqi.configure({
        agentUrl: "http://localhost:3002/start",
        branding: { position: "bottom-right" },
    });

    Voqi.defineTool({
        name: "create_task",
        description: "Create a new task. Used when the user says 'create' or 'add'…",
        parameters: {
            type: "object",
            properties: {
                title: { type: "string" },
                priority: { enum: ["urgent", "high", "medium", "low"] },
            },
            required: ["title"],
        },
        execute: async ({ title, priority }) => {
            const id = useStore.getState().createTask({ title, priority });
            return { id, title };
        },
        requiresConfirmation: false,
    });
});
```

The agent sees `description` and `parameters` on every turn — write
them like you'd write a prompt: be explicit about *when* to use the
tool and what each argument means. Vague descriptions get vague
behaviour. See [`docs/tools.md`](../../docs/tools.md) at the repo root
for the full guide.
