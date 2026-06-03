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
    AeliosSparkMountOptions,
    AeliosSpark,
    AeliosSparkToolDefinition,
    AeliosSparkUserConfig,
} from "./types";

/**
 * Entry point compiled into ``aelios-spark-widget.js`` (IIFE). The host page
 * loads this script once. It registers ``window.AeliosSpark``, drains any
 * callbacks queued in ``window.AeliosSparkReady``, and auto-mounts unless
 * told otherwise.
 *
 * Wiring expected from the host:
 *
 *     <script src=".../aelios-spark-widget.js"
 *             data-agent-url="http://localhost:3002"></script>
 *     <script>
 *       AeliosSpark.configure({
 *         agentUrl: "http://localhost:3002",
 *         branding: { position: "bottom-right" },
 *       });
 *       AeliosSpark.defineTool({ name: "create_task", ... });
 *     </script>
 *
 * The agent server enforces an Origin allowlist (the browser sends
 * Origin automatically), so there's no API key to coordinate — just
 * make sure your host page's origin is in ``AELIOS_SPARK_ALLOWED_ORIGINS`` on
 * the agent server.
 */

interface MountedState {
    container: HTMLElement;
    root: Root;
    setOpen: (open: boolean) => void;
}

const registry = new ToolRegistry();
let mounted: MountedState | null = null;
let userConfig: AeliosSparkUserConfig | null = null;
const ROOT_DATASET_KEY = "aeliosSparkHost";

// AeliosSpark is desktop-only today (small touch viewports make the pill
// useless and the cursor/screenshot model assumes a pointer device).
// Anything narrower than this is treated as mobile: mount is skipped,
// and a live session is torn down if the viewport shrinks past it.
// When the viewport returns to desktop size, the widget re-mounts
// with the same options the consumer originally asked for.
const MIN_DESKTOP_WIDTH_PX = 768;
const RESIZE_DEBOUNCE_MS = 200;

// Remembered intent across viewport changes:
// - ``wantedToMount`` is set whenever the consumer (auto-mount or a
//   programmatic ``AeliosSpark.mount(...)``) asked for a widget, regardless
//   of whether the mount actually proceeded. The resize handler uses
//   this to know whether to re-mount on the desktop transition.
// - ``lastMountOpts`` holds the most recent options so the re-mount
//   is faithful to what was originally requested (mock mode, custom
//   container, etc.).
// - ``AeliosSpark.unmount()`` clears ``wantedToMount`` (an explicit teardown
//   stays torn down); the resize handler uses ``tearDownInternal()``
//   which preserves the intent.
let wantedToMount = false;
let lastMountOpts: AeliosSparkMountOptions = {};
let resizeDebounce: ReturnType<typeof setTimeout> | null = null;

function isMobileViewport(): boolean {
    if (typeof window === "undefined") return false;
    return window.innerWidth < MIN_DESKTOP_WIDTH_PX;
}

function findScriptTag(): HTMLScriptElement | null {
    const all = Array.from(
        document.querySelectorAll(
            'script[data-agent-url], script[src*="aelios-spark-widget"]',
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

function configure(config: AeliosSparkUserConfig): void {
    userConfig = { ...userConfig, ...config };
}

function mount(opts: AeliosSparkMountOptions = {}): void {
    console.info("[AeliosSpark] mount() called", { opts, alreadyMounted: !!mounted });
    // Record intent regardless of whether the mount proceeds — the
    // resize handler reads this to decide whether to bring the widget
    // back when the viewport returns to desktop size.
    wantedToMount = true;
    lastMountOpts = opts;

    if (mounted) return;

    if (isMobileViewport()) {
        console.info(
            `[AeliosSpark] mobile viewport detected (<${MIN_DESKTOP_WIDTH_PX}px) — ` +
                "widget will mount when the viewport reaches desktop size.",
        );
        return;
    }

    const scriptTag = findScriptTag();
    const mockMode = resolveMockMode(scriptTag, opts.mock);
    const agentUrl = mockMode ? "" : resolveAgentUrl(scriptTag);
    if (!mockMode && !agentUrl) {
        console.error(
            "[AeliosSpark] cannot mount widget without an agent URL. Set " +
                "data-agent-url on the script tag, or call " +
                "AeliosSpark.configure({ agentUrl: '...' }) before mount.",
        );
        return;
    }

    const effectiveConfig: AeliosSparkUserConfig = {
        ...(userConfig ?? { agentUrl }),
        agentUrl,
    };

    if (mockMode) {
        console.info(
            "[AeliosSpark] mock mode enabled — no agent server required. " +
                "Manual control surface at window.__aeliosSparkMock.",
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
        console.error("[AeliosSpark] React render threw", err);
    }
    console.info("[AeliosSpark] mount() complete", {
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

function tearDownInternal(): void {
    if (!mounted) return;
    try {
        mounted.root.unmount();
    } catch (err) {
        console.warn("[AeliosSpark] unmount threw", err);
    }
    if (mounted.container.dataset[ROOT_DATASET_KEY] === "1") {
        mounted.container.remove();
    }
    mounted = null;
}

function unmount(): void {
    // Explicit consumer unmount clears the mount intent so a subsequent
    // viewport-resize back to desktop does NOT re-summon the widget.
    wantedToMount = false;
    tearDownInternal();
}

const api: AeliosSpark = {
    configure,
    defineTool: (def: AeliosSparkToolDefinition) => registry.define(def),
    mount: (opts?: AeliosSparkMountOptions) => mount(opts),
    unmount,
    open: () => mounted?.setOpen(true),
    close: () => mounted?.setOpen(false),
};

// Drain any callbacks the host queued before the script finished loading.
const existingQueue = window.AeliosSparkReady ?? [];
window.AeliosSpark = api;
window.AeliosSparkReady = {
    push: (...fns: Array<(api: AeliosSpark) => void>) => {
        for (const fn of fns) {
            try {
                fn(api);
            } catch (err) {
                console.error("[AeliosSpark] queued init callback threw", err);
            }
        }
        return 0;
    },
} as unknown as Array<(api: AeliosSpark) => void>;
for (const fn of existingQueue) {
    try {
        fn(api);
    } catch (err) {
        console.error("[AeliosSpark] queued init callback threw", err);
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

// Bidirectional viewport tracking:
//   * desktop -> mobile while mounted: tear down (drops the live
//     WebRTC session) but keep the consumer's mount intent so the
//     widget comes back automatically the moment the viewport is
//     desktop-sized again.
//   * mobile -> desktop while not mounted but intent is still set
//     (auto-mount fired on a mobile load, or a previous resize tore
//     it down): re-mount with the originally requested options.
// Debounced because drag-resize / devtools toggle fire `resize` many
// times per second; mount/unmount thrash would be terrible.
if (typeof window !== "undefined") {
    window.addEventListener("resize", () => {
        if (resizeDebounce !== null) clearTimeout(resizeDebounce);
        resizeDebounce = setTimeout(() => {
            resizeDebounce = null;
            const mobile = isMobileViewport();
            if (mobile && mounted) {
                console.info(
                    "[AeliosSpark] viewport shrank below desktop threshold — " +
                        "tearing down widget; will re-mount when desktop-sized.",
                );
                tearDownInternal();
            } else if (!mobile && !mounted && wantedToMount) {
                console.info(
                    "[AeliosSpark] viewport returned to desktop size — re-mounting widget.",
                );
                mount(lastMountOpts);
            }
        }, RESIZE_DEBOUNCE_MS);
    });
}

export default api;
