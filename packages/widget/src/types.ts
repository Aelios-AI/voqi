/**
 * Public types exposed to host pages via `window.AeliosSpark`.
 *
 * The host integrator sees only what's in here — keep it minimal so we can
 * evolve internals without breaking embeds.
 */

export interface AeliosSparkToolDefinition {
    /** Stable identifier the agent uses to invoke this tool. */
    name: string;
    /**
     * The agent reads this every time it considers calling the tool.
     * Tell it WHEN to call, what it does, and any side effects. Treat
     * like prompt engineering — vague descriptions get vague behaviour.
     */
    description: string;
    /**
     * JSON Schema describing this tool's args. Always
     * `{ type: "object", properties: {...} }`. Use enums liberally;
     * they massively improve the agent's argument formatting.
     */
    parameters: Record<string, unknown>;
    /**
     * The function the agent calls. Receives args matching
     * `parameters`. Whatever you return becomes what the agent
     * sees on its next turn — be generous, return rich data.
     * Throwing surfaces the error to the agent.
     */
    execute: (args: Record<string, unknown>) => Promise<unknown> | unknown;
    /**
     * When true, the agent must ask the visitor's permission before
     * calling. The widget shows a built-in confirmation prompt.
     * Use for destructive / irreversible actions.
     */
    requiresConfirmation?: boolean;
}

/**
 * Host-supplied configuration. Pass this to ``AeliosSpark.configure({...})``
 * before the widget mounts. Everything except ``agentUrl`` is optional.
 *
 * Only fields the widget actually renders are accepted. The language
 * picker is hardcoded to a 37-language list inside the widget; agent
 * name / avatar / welcome message / named themes / software name /
 * logo are not currently wired up on the widget side.
 */
export interface AeliosSparkUserConfig {
    /** URL of the running AeliosSpark agent server (e.g. http://localhost:3002). */
    agentUrl: string;
    branding?: {
        /** Pill anchor — bottom-right (default) or bottom-left. */
        position?: WidgetPosition;
        /** Override the widget's CSS palette directly. */
        themeColors?: InAppWidgetThemeColors;
    };
}

export interface AeliosSparkMountOptions {
    /** DOM node to mount into. Defaults to a new <div> appended to body. */
    container?: HTMLElement;
    /**
     * UI-only test mode. When true, both the local API and the realtime
     * transport are replaced with deterministic in-memory fakes — no
     * network calls, no agent server required. A manual control surface
     * is exposed at ``window.__aeliosSparkMock`` for driving widget state from
     * the browser console. Can also be enabled via ``data-mock="true"``
     * on the script tag.
     */
    mock?: boolean;
}

export interface AeliosSpark {
    configure: (config: AeliosSparkUserConfig) => void;
    defineTool: (def: AeliosSparkToolDefinition) => void;
    mount: (opts?: AeliosSparkMountOptions) => void;
    unmount: () => void;
    open: () => void;
    close: () => void;
    /** Internal: queue drained on load. Host pushes init callbacks here. */
    _queue?: Array<(api: AeliosSpark) => void>;
}

/**
 * Manual control surface exposed at ``window.__aeliosSparkMock`` when the
 * widget is mounted in mock mode.
 */
export interface AeliosSparkMockControl {
    simulateUserSpeech: (text: string) => void;
    simulateBotMessage: (text: string) => void;
    fireAgentReady: () => void;
    setAgentState: (state: "listening" | "processing" | "responding" | "idle") => void;
    simulateToolCall: (
        name: string,
        args?: Record<string, unknown>,
    ) => Promise<unknown>;
    disconnect: () => void;
    simulateConnectionLoss: () => void;
    simulateError: (message: string) => void;
    setAutoBehavior: (opts: { autoGreet?: boolean; autoReady?: boolean }) => void;
    triggerScreenshotCapture: () => Promise<
        | { ok: true; image_b64: string; mime: string; width: number; height: number }
        | { ok: false; error: string }
    >;
    simulateIdleTimeoutEnd: () => void;
    simulatePermissionDenied: (message?: string) => void;
    simulateGuideCursor: (
        x?: number,
        y?: number,
        label?: string,
    ) => void;
}

declare global {
    interface Window {
        AeliosSpark?: AeliosSpark;
        AeliosSparkReady?: Array<(api: AeliosSpark) => void>;
        __aeliosSparkMock?: AeliosSparkMockControl;
    }
}

export interface InAppWidgetTool {
    name: string;
    description: string;
    parametersJsonSchema: Record<string, unknown>;
    requiresConfirmation: boolean;
}

/** Where the widget anchors itself in the viewport. Only the two
 *  bottom corners are supported — the transcript popover and the
 *  caption stack both expand UPWARD from the pill, so a top anchor
 *  would push them off the top of the viewport. */
export type WidgetPosition = "bottom-right" | "bottom-left";

/** Runtime mode the visitor selected at session start. ``action`` is the
 *  full agent that calls host tools. ``guide`` is read-only — sees the
 *  screen on every turn, may point with a ghost cursor, but cannot
 *  call tools. */
export type InAppMode = "action" | "guide";

export interface InAppWidgetThemeColors {
    primary: string;
    bg: string;
    text: string;
    muted: string;
    onPrimary: string;
}

/**
 * Resolved config the widget's React tree reads. Only fields the
 * widget actually renders are here — see AeliosSparkUserConfig for the
 * host-facing surface that maps into this.
 */
export interface InAppWidgetConfig {
    branding: {
        launcherShape?: "pill" | "square" | "circle";
        position?: WidgetPosition;
    } | null;
    themeColors?: InAppWidgetThemeColors | null;
}

/**
 * What the local API returns when the widget starts a session. The
 * ``tools/language/mode/agentUrl`` fields are OSS-only — they get
 * forwarded straight to the agent server in the /start POST body.
 */
export interface InAppSessionStart {
    sessionUuid: string;
    inAppAgentUuid: string;
    token: { token: string; expiresIn: number };
    tools?: InAppWidgetTool[];
    language?: string;
    mode?: InAppMode;
    agentUrl?: string;
}

/**
 * Tool round-trip messages — exchanged with the agent server over the
 * RTVI custom-message channel. Keep these names stable; the Python side
 * matches on them.
 */
export interface ToolCallBatchServerMessage {
    type: "tool_call_batch";
    batch_id: string;
    tool_calls: Array<{
        call_id: string;
        name: string;
        args: Record<string, unknown>;
    }>;
}

export interface ToolResultClientMessage {
    type: "tool_result";
    batch_id: string;
    call_id: string;
    result?: unknown;
    error?: string;
}

/**
 * Guide-mode pointing hint. Pushed by the bot (server → widget) when
 * the LLM populates ``point_to`` on a screenshot_result round.
 */
export interface GuideCursorServerMessage {
    type: "guide_cursor";
    x: number;
    y: number;
    label: string;
}
