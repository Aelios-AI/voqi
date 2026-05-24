import { createRoot, type Root } from "react-dom/client";
import { useState, useEffect } from "react";
import { LocalInAppApi, type IInAppApi } from "./api";
import { InAppTransport } from "./transport";
import { MockInAppApi } from "./mockApi";
import { MockInAppTransport } from "./mockTransport";
import { ToolRegistry } from "./toolRegistry";
import { Widget, type TransportFactory } from "./Widget";
import { WIDGET_CSS } from "./styles";
import type {
    VoqiMountOptions,
    Voqi,
    VoqiToolDefinition,
    VoqiUserConfig,
} from "./types";

/**
 * Entry point compiled into ``voqi-widget.js`` (IIFE). The host page
 * loads this script once. It registers ``window.Voqi``, drains any
 * callbacks queued in ``window.VoqiReady``, and auto-mounts unless
 * told otherwise.
 *
 * Wiring expected from the host:
 *
 *     <script src=".../voqi-widget.js"
 *             data-agent-url="http://localhost:3002"></script>
 *     <script>
 *       Voqi.configure({
 *         agentUrl: "http://localhost:3002",
 *         branding: { position: "bottom-right" },
 *       });
 *       Voqi.defineTool({ name: "create_task", ... });
 *     </script>
 *
 * The agent server enforces an Origin allowlist (the browser sends
 * Origin automatically), so there's no API key to coordinate — just
 * make sure your host page's origin is in ``VOQI_ALLOWED_ORIGINS`` on
 * the agent server.
 */

interface MountedState {
    container: HTMLElement;
    root: Root;
    setOpen: (open: boolean) => void;
}

const registry = new ToolRegistry();
let mounted: MountedState | null = null;
let userConfig: VoqiUserConfig | null = null;
const ROOT_DATASET_KEY = "voqiHost";

function findScriptTag(): HTMLScriptElement | null {
    const all = Array.from(
        document.querySelectorAll(
            'script[data-agent-url], script[src*="voqi-widget"]',
        ),
    );
    if (all.length === 0) return null;
    return all[all.length - 1] as HTMLScriptElement;
}

function resolveAgentUrl(scriptTag: HTMLScriptElement | null): string {
    if (userConfig?.agentUrl) return userConfig.agentUrl;
    return scriptTag?.getAttribute("data-agent-url") ?? "";
}

function resolveMockMode(
    scriptTag: HTMLScriptElement | null,
    override?: boolean,
): boolean {
    if (typeof override === "boolean") return override;
    const attr = scriptTag?.getAttribute("data-mock");
    return attr === "true" || attr === "1";
}

function configure(config: VoqiUserConfig): void {
    userConfig = { ...userConfig, ...config };
}

function mount(opts: VoqiMountOptions = {}): void {
    console.info("[Voqi] mount() called", { opts, alreadyMounted: !!mounted });
    if (mounted) return;

    const scriptTag = findScriptTag();
    const mockMode = resolveMockMode(scriptTag, opts.mock);
    const agentUrl = mockMode ? "" : resolveAgentUrl(scriptTag);
    if (!mockMode && !agentUrl) {
        console.error(
            "[Voqi] cannot mount widget without an agent URL. Set " +
                "data-agent-url on the script tag, or call " +
                "Voqi.configure({ agentUrl: '...' }) before mount.",
        );
        return;
    }

    const effectiveConfig: VoqiUserConfig = {
        ...(userConfig ?? { agentUrl }),
        agentUrl,
    };

    if (mockMode) {
        console.info(
            "[Voqi] mock mode enabled — no agent server required. " +
                "Manual control surface at window.__voqiMock.",
        );
    }

    const container = opts.container ?? document.createElement("div");
    if (!opts.container) {
        container.style.position = "fixed";
        container.style.left = "0";
        container.style.top = "0";
        container.style.width = "0";
        container.style.height = "0";
        container.style.zIndex = "2147483646";
        container.dataset[ROOT_DATASET_KEY] = "1";
        document.body.appendChild(container);
    }

    const shadow = container.attachShadow({ mode: "open" });
    const styleTag = document.createElement("style");
    styleTag.textContent = WIDGET_CSS;
    shadow.appendChild(styleTag);

    const reactRoot = document.createElement("div");
    shadow.appendChild(reactRoot);

    const api: IInAppApi = mockMode
        ? new MockInAppApi()
        : new LocalInAppApi(effectiveConfig);

    const transportFactory: TransportFactory = mockMode
        ? (registry, events) => new MockInAppTransport(registry, events)
        : (registry, events) => new InAppTransport(agentUrl, registry, events);

    let setOpenFn: (open: boolean) => void = () => {};
    const root = createRoot(reactRoot);

    function WidgetHost() {
        const [open, setOpen] = useState(false);
        useEffect(() => {
            setOpenFn = setOpen;
        }, []);
        return (
            <Widget
                api={api}
                transportFactory={transportFactory}
                registry={registry}
                initiallyOpen={false}
                isOpen={open}
                onCloseRequest={() => setOpen((v) => !v)}
            />
        );
    }

    try {
        root.render(<WidgetHost />);
    } catch (err) {
        console.error("[Voqi] React render threw", err);
    }
    console.info("[Voqi] mount() complete", {
        container,
        hasShadow: !!container.shadowRoot,
        mockMode,
    });

    mounted = {
        container,
        root,
        setOpen: (open) => setOpenFn(open),
    };
}

function unmount(): void {
    if (!mounted) return;
    try {
        mounted.root.unmount();
    } catch (err) {
        console.warn("[Voqi] unmount threw", err);
    }
    if (mounted.container.dataset[ROOT_DATASET_KEY] === "1") {
        mounted.container.remove();
    }
    mounted = null;
}

const api: Voqi = {
    configure,
    defineTool: (def: VoqiToolDefinition) => registry.define(def),
    mount: (opts?: VoqiMountOptions) => mount(opts),
    unmount,
    open: () => mounted?.setOpen(true),
    close: () => mounted?.setOpen(false),
};

// Drain any callbacks the host queued before the script finished loading.
const existingQueue = window.VoqiReady ?? [];
window.Voqi = api;
window.VoqiReady = {
    push: (...fns: Array<(api: Voqi) => void>) => {
        for (const fn of fns) {
            try {
                fn(api);
            } catch (err) {
                console.error("[Voqi] queued init callback threw", err);
            }
        }
        return 0;
    },
} as unknown as Array<(api: Voqi) => void>;
for (const fn of existingQueue) {
    try {
        fn(api);
    } catch (err) {
        console.error("[Voqi] queued init callback threw", err);
    }
}

// Auto-mount unless explicitly opted out via `data-auto-mount="false"`.
const scriptTag = findScriptTag();
const autoMount = scriptTag?.getAttribute("data-auto-mount") !== "false";
if (autoMount) {
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => mount(), {
            once: true,
        });
    } else {
        mount();
    }
}

export default api;
