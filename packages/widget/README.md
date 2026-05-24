# voqi-widget

The browser-side of Voqi. A single IIFE bundle that drops onto any web
page and exposes `window.Voqi` for tool registration + branding config.

## Build

```bash
npm install
npm run build
# → dist/voqi-widget.js
```

`npm run dev` rebuilds on save.

## What it does

- Renders the collapsible voice pill (Shadow-DOM scoped so host CSS
  can't bleed in)
- Manages the connection lifecycle to the agent server (Daily WebRTC,
  with cold-start retry)
- Hosts the tool registry — host pages register callable functions via
  `Voqi.defineTool({...})`
- Captures DOM screenshots on demand (lazy-loaded `html2canvas-pro`)
  for screen-aware modes
- Renders the transcript bubble + status states (listening, thinking,
  responding)

## Public surface

```ts
// Global, set by the loaded bundle
window.Voqi = {
    configure(config: VoqiUserConfig): void;
    defineTool(def: VoqiToolDefinition): void;
    mount(opts?: VoqiMountOptions): void;
    unmount(): void;
    open(): void;
    close(): void;
};

// Pre-load queue — push callbacks that run when the bundle finishes
window.VoqiReady = window.VoqiReady ?? [];
window.VoqiReady.push((Voqi) => { /* ... */ });
```

See [`src/types.ts`](src/types.ts) for full type definitions and
[`docs/configuration.md`](../../docs/configuration.md) for the config
schema.

## Embedding

```html
<script src="/voqi-widget.js"
        data-agent-url="http://localhost:3002/start"
        data-auto-mount="true"
        data-mock="false"></script>
```

| Attribute | Notes |
|---|---|
| `data-agent-url` | Required. Where the agent server's `/start` lives. |
| `data-auto-mount` | Defaults to `true`. Set `false` to call `Voqi.mount()` yourself. |
| `data-mock` | Set `true` for UI-only mode (no network, no agent). |

Auth between widget and agent server is purely origin-based — set
`VOQI_ALLOWED_ORIGINS` on the agent server to the domains hosting your
widget, and the browser does the rest. No tokens to coordinate.

Anything you'd set via attributes can equivalently be set with
`Voqi.configure({...})` — see [`docs/configuration.md`](../../docs/configuration.md).

## Mock mode

```html
<script src="/voqi-widget.js" data-mock="true"></script>
```

Replaces the API + transport with deterministic fakes. Use it to
exercise the widget UI without running the agent server. A console
control surface lives at `window.__voqiMock` — see
[`src/types.ts`](src/types.ts) for the available helpers.

## Bundling notes

The build emits a single self-contained IIFE — React, Pipecat,
html2canvas-pro are all inlined. No peer deps on the host page. CSS is
inlined as a string and injected into the Shadow Root at mount.
