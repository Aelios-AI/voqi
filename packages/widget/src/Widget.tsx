import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { PipecatClient } from "@pipecat-ai/client-js";
import {
    PipecatClientAudio,
    PipecatClientProvider,
    usePipecatClientMediaTrack,
} from "@pipecat-ai/client-react";
import type { IInAppApi } from "./api";
import {
    type IInAppTransport,
    type TranscriptEntry,
    type TransportEvents,
} from "./transport";
import { ToolRegistry } from "./toolRegistry";
import type {
    InAppMode,
    InAppSessionStart,
    InAppWidgetConfig,
} from "./types";

// Hardcoded language list — the full 37 entries Voqi supports. The
// widget's UI is intentionally decoupled from whatever subset the
// agent profile may declare: the visitor always sees the full menu
// (fall-back voices + STT cover languages the agent profile doesn't
// have a dedicated voice for).
const LANGUAGE_OPTIONS: Array<{
    value: string;
    label: string;
    flag: string;
}> = [
    { value: "ar", label: "Arabic", flag: "\u{1F1F8}\u{1F1E6}" },
    { value: "bg", label: "Bulgarian", flag: "\u{1F1E7}\u{1F1EC}" },
    { value: "zh", label: "Chinese", flag: "\u{1F1E8}\u{1F1F3}" },
    { value: "hr", label: "Croatian", flag: "\u{1F1ED}\u{1F1F7}" },
    { value: "cs", label: "Czech", flag: "\u{1F1E8}\u{1F1FF}" },
    { value: "da", label: "Danish", flag: "\u{1F1E9}\u{1F1F0}" },
    { value: "nl", label: "Dutch", flag: "\u{1F1F3}\u{1F1F1}" },
    { value: "en", label: "English", flag: "\u{1F1FA}\u{1F1F8}" },
    { value: "fi", label: "Finnish", flag: "\u{1F1EB}\u{1F1EE}" },
    { value: "fr", label: "French", flag: "\u{1F1EB}\u{1F1F7}" },
    { value: "de", label: "German", flag: "\u{1F1E9}\u{1F1EA}" },
    { value: "el", label: "Greek", flag: "\u{1F1EC}\u{1F1F7}" },
    { value: "gu", label: "Gujarati", flag: "\u{1F1EE}\u{1F1F3}" },
    { value: "he", label: "Hebrew", flag: "\u{1F1EE}\u{1F1F1}" },
    { value: "hi", label: "Hindi", flag: "\u{1F1EE}\u{1F1F3}" },
    { value: "hu", label: "Hungarian", flag: "\u{1F1ED}\u{1F1FA}" },
    { value: "id", label: "Indonesian", flag: "\u{1F1EE}\u{1F1E9}" },
    { value: "it", label: "Italian", flag: "\u{1F1EE}\u{1F1F9}" },
    { value: "ja", label: "Japanese", flag: "\u{1F1EF}\u{1F1F5}" },
    { value: "kn", label: "Kannada", flag: "\u{1F1EE}\u{1F1F3}" },
    { value: "ko", label: "Korean", flag: "\u{1F1F0}\u{1F1F7}" },
    { value: "ms", label: "Malay", flag: "\u{1F1F2}\u{1F1FE}" },
    { value: "mr", label: "Marathi", flag: "\u{1F1EE}\u{1F1F3}" },
    { value: "no", label: "Norwegian", flag: "\u{1F1F3}\u{1F1F4}" },
    { value: "pl", label: "Polish", flag: "\u{1F1F5}\u{1F1F1}" },
    { value: "pt", label: "Portuguese", flag: "\u{1F1F5}\u{1F1F9}" },
    { value: "ro", label: "Romanian", flag: "\u{1F1F7}\u{1F1F4}" },
    { value: "ru", label: "Russian", flag: "\u{1F1F7}\u{1F1FA}" },
    { value: "sk", label: "Slovak", flag: "\u{1F1F8}\u{1F1F0}" },
    { value: "es", label: "Spanish", flag: "\u{1F1EA}\u{1F1F8}" },
    { value: "sv", label: "Swedish", flag: "\u{1F1F8}\u{1F1EA}" },
    { value: "tl", label: "Tagalog", flag: "\u{1F1F5}\u{1F1ED}" },
    { value: "ta", label: "Tamil", flag: "\u{1F1EE}\u{1F1F3}" },
    { value: "te", label: "Telugu", flag: "\u{1F1EE}\u{1F1F3}" },
    { value: "th", label: "Thai", flag: "\u{1F1F9}\u{1F1ED}" },
    { value: "tr", label: "Turkish", flag: "\u{1F1F9}\u{1F1F7}" },
    { value: "vi", label: "Vietnamese", flag: "\u{1F1FB}\u{1F1F3}" },
];

// Browser-language auto-detect: if the visitor's browser locale's
// primary subtag matches one of our supported codes, pre-select it;
// else fall back to English.
function detectBrowserLanguage(): string {
    try {
        const browserLang = navigator.language.split("-")[0].toLowerCase();
        const matched = LANGUAGE_OPTIONS.find((opt) => opt.value === browserLang);
        return matched ? matched.value : "en";
    } catch {
        return "en";
    }
}

/** Factory that builds either the real or mock transport. The Widget
 *  doesn't know or care which one it gets. */
export type TransportFactory = (
    registry: ToolRegistry,
    events: TransportEvents,
) => IInAppTransport;

type AgentState = "idle" | "listening" | "processing" | "responding" | "error";
type ConnectionPhase =
    | "fresh"               // never connected this open-cycle
    | "connecting"          // /start posted, waiting for WebRTC + BotReady
    | "ready"               // BotReady received — agent can converse
    | "ended"               // user clicked End / closed the panel
    | "failed"              // never reached a working session (main-backend
                            // /session call errored, agent-backend polling
                            // ran out of budget, or the connecting
                            // watchdog elapsed without BotReady)
    | "lost"                // had a working session and the connection
                            // dropped mid-conversation (Daily WebRTC
                            // unexpected_disconnect)
    | "permission_denied";  // browser/OS denied mic access — visitor must fix it

// How long the floating caption stack (last-two assistant turns shown
// above the pill when history is closed) lingers after the agent has
// fully finished speaking. The timer ONLY starts once the assistant
// turn ends — while sentences are still streaming in, the stack stays
// up indefinitely. 20 seconds is a comfortable read window for a
// 3-sentence answer without anchoring the caption to the page forever.
const CAPTION_LINGER_MS = 20_000;
// Hard cap on the transcript log. With coalescing, 200 raw entries is
// roughly 30-40 distinct exchanges — comfortably more than fits on
// screen, the popover scrolls.
const TRANSCRIPT_HARD_CAP = 200;

// Session-length contract:
// * Backend kills the realtime bot at 100 minutes (hard backstop).
// * Widget self-terminates at 90 minutes — silent close, no advance
//   warning. Hitting the cap shows a short "Session ended" hint that
//   auto-clears in 10s. The visitor restarts via the launcher.
const SESSION_HARD_CAP_MS = 90 * 60 * 1000;
// Session-ending hint auto-clears after this so the widget never
// sits on a stale banner.
const ENDING_HINT_AUTO_CLEAR_MS = 10_000;

// If we land in "connecting" but the bot's agent_ready signal never
// arrives within this window — agent crashed during init, LLM cold
// start hung, kickoff frame lost — we flip the pill to a lost state
// so the visitor can End and try again instead of staring at
// "Connecting…" forever. Sized to cover the transport's 5-minute
// /start polling budget plus a 1-minute buffer for the bot to fully
// boot after /start succeeds; anything tighter would race a
// legitimately slow cold start.
const CONNECTING_TIMEOUT_MS = 6 * 60 * 1000;

/**
 * Voqi spectrum glyph — 5-bar wave used as the idle/live/busy/responding
 * indicator in the pill. The signature look that says "voice assistant".
 */
function SpectrumGlyph({ size = 16 }: { size?: number }) {
    const heights = [3.5, 8, 11.5, 8, 5];
    const barWidth = 1.5;
    const gap = 0.9;
    const total = heights.length * barWidth + (heights.length - 1) * gap;
    const startX = (14 - total) / 2;
    return (
        <svg width={size} height={size} viewBox="0 0 14 14" aria-hidden="true" fill="currentColor">
            {heights.map((h, i) => (
                <rect
                    key={i}
                    x={startX + i * (barWidth + gap)}
                    y={(14 - h) / 2}
                    width={barWidth}
                    height={h}
                    rx={barWidth / 2}
                />
            ))}
        </svg>
    );
}

/**
 * Indicator swapped into the pill's action button by current state.
 * The spectrum glyph is the "default" look (idle / live / responding);
 * we swap it for a connecting spinner, listening mic, or error glyph
 * when the agent's state needs a different read.
 */
function PillIndicator({
    state,
}: {
    state:
        | "idle"
        | "connecting"
        | "live"
        | "listening"
        | "working"
        | "error";
}) {
    if (state === "connecting") {
        return (
            <svg viewBox="0 0 16 16" aria-hidden="true">
                <circle cx="3" cy="8" r="1.4" fill="currentColor">
                    <animate attributeName="opacity" values="0.3;1;0.3" dur="1.2s" repeatCount="indefinite" begin="0s" />
                </circle>
                <circle cx="8" cy="8" r="1.4" fill="currentColor">
                    <animate attributeName="opacity" values="0.3;1;0.3" dur="1.2s" repeatCount="indefinite" begin="0.2s" />
                </circle>
                <circle cx="13" cy="8" r="1.4" fill="currentColor">
                    <animate attributeName="opacity" values="0.3;1;0.3" dur="1.2s" repeatCount="indefinite" begin="0.4s" />
                </circle>
            </svg>
        );
    }
    if (state === "listening") {
        return (
            <svg viewBox="0 0 16 16" aria-hidden="true">
                <rect x="5.25" y="2.25" width="5.5" height="6.5" rx="2.75" fill="none" stroke="currentColor" strokeWidth="1.5" />
                <path d="M3.75 7.75a4.25 4.25 0 0 0 8.5 0M8 12v1.75M5.5 13.75h5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
            </svg>
        );
    }
    if (state === "error") {
        return (
            <svg viewBox="0 0 16 16" aria-hidden="true">
                <circle cx="8" cy="11.75" r="1" fill="currentColor" />
                <path d="M8 3.25v5.75" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
            </svg>
        );
    }
    // idle / live / working all use the signature spectrum glyph —
    // the chrome (status-dot colour, glow) tells the visitor what's
    // happening, the indicator stays consistent.
    return <SpectrumGlyph size={16} />;
}

function SendGlyph() {
    return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
        </svg>
    );
}

function EndGlyph() {
    // Phone handset rotated downward — universal "end call".
    return (
        <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
        >
            <path
                transform="rotate(135 12 12)"
                d="M5 4h3l1.5 4-2 1.2a11 11 0 0 0 5.3 5.3l1.2-2 4 1.5v3a2 2 0 0 1-2 2A14 14 0 0 1 3 6a2 2 0 0 1 2-2z"
            />
        </svg>
    );
}

/**
 * Chevron glyph used for the in-pill minimize button and the bottom-
 * anchored restore tab. Minimize tucks the widget DOWN off-screen, so
 * the in-pill chevron points down; the restore tab points up, telling
 * the visitor "click to bring the assistant back up".
 */
function ChevronGlyph({
    direction,
}: {
    direction: "up" | "down" | "left" | "right";
}) {
    const points: Record<typeof direction, string> = {
        right: "9 6 15 12 9 18",
        left: "15 6 9 12 15 18",
        down: "6 9 12 15 18 9",
        up: "6 15 12 9 18 15",
    };
    return (
        <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.4"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
        >
            <polyline points={points[direction]} />
        </svg>
    );
}

function TranscriptGlyph({ open }: { open: boolean }) {
    // Lines-of-text icon with a small bottom-corner caret indicating
    // open/closed. Way clearer than a bare chevron for "view past
    // messages".
    return (
        <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
        >
            <line x1="6" y1="8" x2="18" y2="8" />
            <line x1="6" y1="12" x2="18" y2="12" />
            <line x1="6" y1="16" x2="14" y2="16" />
            {open && <circle cx="19" cy="17" r="1.6" fill="currentColor" stroke="none" />}
        </svg>
    );
}

interface WidgetProps {
    api: IInAppApi;
    /** Builds the realtime transport. In production this builds a
     *  Pipecat/Daily transport pointed at ``voiceAgentUrl``; in mock
     *  mode it builds an in-memory fake. */
    transportFactory: TransportFactory;
    registry: ToolRegistry;
    initiallyOpen: boolean;
    onCloseRequest: () => void;
    /** Imperative open/close from the global API. */
    isOpen: boolean;
}

/**
 * Top-level widget React component, rendered into a Shadow DOM root. Owns
 * config fetch, session lifecycle, and the launcher/panel UI. Tools are
 * dispatched by the transport via the shared ToolRegistry — this component
 * only renders state.
 */
export function Widget({
    api,
    transportFactory,
    registry,
    onCloseRequest,
    isOpen,
}: WidgetProps) {
    const [config, setConfig] = useState<InAppWidgetConfig | null>(null);
    const [configError, setConfigError] = useState<string | null>(null);
    const [session, setSession] = useState<InAppSessionStart | null>(null);
    const [agentState, setAgentState] = useState<AgentState>("idle");
    const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
    const [isStarting, setIsStarting] = useState(false);
    const [connectionPhase, setConnectionPhase] = useState<ConnectionPhase>("fresh");
    const [sessionEndingHint, setSessionEndingHint] = useState<string | null>(null);
    // Start muted. The bot greets first; a brand-new visitor doesn't
    // need their mic open until they choose to talk back, and the
    // kickoff message tells them how to unmute or switch to typing.
    // The actual Daily track gets muted in ``onBotReady`` below so
    // the React state and the underlying microphone track stay in
    // sync from the moment the bot is ready.
    const [isMuted, setIsMuted] = useState(true);
    // Agent audio mute. Default = unmuted so the visitor hears the
    // agent's voice on a fresh session. Toggling this mutes the
    // inbound bot audio CLIENT-SIDE only — the bot keeps producing
    // TTS server-side and the transcript still streams down. The
    // mute effect walks every <audio> element on the host page and
    // flips ``.muted``.
    const [isAgentMuted, setIsAgentMuted] = useState(false);
    // Hold the underlying PipecatClient so we can hand it to
    // ``<PipecatClientProvider>`` and let ``<PipecatClientAudio />``
    // create the actual ``<audio>`` element that plays the bot's
    // inbound audio track. Without this React component the bot's
    // TTS reaches the browser via Daily but has no DOM element to
    // route through — silence. Mock transport returns null here, so
    // the audio component is skipped in mock mode.
    const [pipecatClient, setPipecatClient] = useState<PipecatClient | null>(
        null,
    );
    const [textInputMode, setTextInputMode] = useState(false);
    const [pendingText, setPendingText] = useState("");
    // Minimized = the entire widget shell (pill + caption stack +
    // text-input row) slides off-screen toward whichever edge it's
    // anchored to, leaving only a thin restore tab pinned to that edge.
    // The session keeps running underneath — listening, processing,
    // tool calls all continue. Visitor restores by clicking the tab.
    const [isMinimized, setIsMinimized] = useState(false);
    // Pre-session picker. Two-step modal: language → mode. Either
    // step is skipped when there's only one valid choice (one
    // configured language, or no host tools so guide mode is forced).
    // ``closed`` means the picker isn't open; this is the resting
    // state both before the visitor clicks Talk and after they finish
    // picking. The visitor's language choice is held in
    // ``pickedLanguage`` while the mode step is in progress so we can
    // pass both into ``startVoice`` together at the end.
    // Single-step picker. The modal opens with the language dropdown
    // pre-set to the visitor's auto-detected default and the mode cards
    // visible inline (when there are tools to choose between). Both
    // choices live in one modal — no two-step flow — so the visitor
    // sees the full picture and just clicks the mode they want to
    // start. Language sits as a compact pill + flag dropdown in the
    // header; the body holds the mode cards.
    const [pickerOpen, setPickerOpen] = useState(false);
    const [pickedLanguage, setPickedLanguage] = useState<string>("en");
    const [languageDropdownOpen, setLanguageDropdownOpen] = useState(false);
    // Search query inside the language dropdown. Filters by label
    // (case-insensitive prefix) or by ISO code prefix so typing "fr"
    // matches both "French" and "fr". Reset whenever the dropdown
    // closes so the next open starts fresh.
    const [languageSearch, setLanguageSearch] = useState("");
    // Guide-mode ghost cursor. Populated when a ``guide_cursor`` server
    // message arrives; cleared 20s later by ``cursorTimerRef``. Stored
    // as normalized [0, 1] coords + label; the overlay multiplies by
    // viewport on render so window resize / scroll while the cursor
    // is visible doesn't desync it from the page.
    const [guideCursor, setGuideCursor] = useState<
        { x: number; y: number; label: string } | null
    >(null);
    const cursorTimerRef = useRef<number | null>(null);
    // Minutes-remaining the expiration banner is currently advertising; null
    // means the banner is hidden (either pre-warning or user-dismissed).
    // We only ever advertise "5 min left" — earlier nags are noise.
    const transportRef = useRef<IInAppTransport | null>(null);
    const sessionStartRef = useRef<string | null>(null);
    const endVoiceRef = useRef<((closePanel: boolean) => Promise<void>) | null>(null);
    // Flag flipped by ``endVoice`` so the in-flight ``startVoice``
    // can tell "user cancelled mid-connect" (silent path) from "main
    // backend errored before we even built a transport" (real error,
    // must surface). Without this, ``transportRef.current === null``
    // is ambiguous: it's null both pre-transport-built and post-
    // user-cancel, and the catch was wrongly classifying API errors
    // as cancellations — leaving the pill stuck in "Connecting…".
    const connectCancelledRef = useRef(false);
    // Mirror of the terminal-state status the transport handlers can read
    // synchronously. Once flipped true (lost / permission_denied / agent
    // error), every late-arriving transport event — transcript chunks,
    // turn_started, etc. — is dropped so the visitor never sees fresh
    // content materialize after the session is visibly dead.
    const sessionDeadRef = useRef(false);

    // Eager config fetch — the pill is visible from page load, so the
    // user can click "Talk" at any time. We need branding + welcome
    // copy in hand BEFORE the click so startSession doesn't no-op on
    // a missing config. (Used to be lazy on panel-open, but the
    // panel-open trigger no longer exists.)
    useEffect(() => {
        if (config || configError) return;
        let cancelled = false;
        api.getConfig()
            .then((c) => {
                if (!cancelled) setConfig(c);
            })
            .catch((err) => {
                if (!cancelled) {
                    console.error("[Voqi] config fetch failed", err);
                    setConfigError(err instanceof Error ? err.message : String(err));
                    // Do NOT flip connectionPhase to "failed" here —
                    // the visitor hasn't clicked anything yet, so
                    // popping a "Connection failed" pill out of
                    // nowhere on a page they didn't even know had a
                    // widget would be confusing. Instead, the
                    // component-level early-return below renders
                    // nothing while configError is set, so the host
                    // page looks like the widget isn't installed.
                    // The console.error above is for the host
                    // integrator who's debugging the embed.
                }
            });
        return () => {
            cancelled = true;
        };
    }, [config, configError, api]);

    // Hard session cap enforced client-side. The agent server has its
    // own 100-minute backstop, but the widget closes silently a bit
    // earlier with a brief "Session ended" hint that auto-clears; the
    // visitor just restarts.
    useEffect(() => {
        if (!session) return;
        const startedAt = sessionStartRef.current
            ? new Date(sessionStartRef.current).getTime()
            : Date.now();
        const ms = (target: number) => Math.max(0, startedAt + target - Date.now());

        const tCap = window.setTimeout(() => {
            setSessionEndingHint(
                "Session ended — 90-minute session cap reached.",
            );
            void endVoiceRef.current?.(false);
        }, ms(SESSION_HARD_CAP_MS));

        return () => {
            window.clearTimeout(tCap);
        };
    }, [session]);

    // Auto-clear the session-ending hint after a short window so we never
    // sit on a stale banner. Visitor can also dismiss it sooner.
    useEffect(() => {
        if (sessionEndingHint === null) return;
        const t = window.setTimeout(
            () => setSessionEndingHint(null),
            ENDING_HINT_AUTO_CLEAR_MS,
        );
        return () => window.clearTimeout(t);
    }, [sessionEndingHint]);

    // Connecting watchdog. We can land in "connecting" indefinitely if
    // the bot accepts the WebRTC handshake but never produces its
    // ``agent_ready`` signal — agent crashed during init, LLM cold
    // start hung, kickoff lost. Without this fallback the visitor
    // would stare at "Connecting…" forever with no exit other than a
    // page refresh. After the window elapses, flip to a "failed"
    // state — we never reached a working session, so this is a
    // failure to connect, NOT a mid-conversation drop.
    useEffect(() => {
        if (connectionPhase !== "connecting") return;
        const t = window.setTimeout(() => {
            setConnectionPhase("failed");
            setAgentState("error");
        }, CONNECTING_TIMEOUT_MS);
        return () => window.clearTimeout(t);
    }, [connectionPhase]);

    const branding = config?.branding ?? null;
    const launcherShape = branding?.launcherShape ?? "pill";
    const position = branding?.position ?? "bottom-right";

    // Theme override emitted as a `<style>` element rendered into the
    // shadow DOM. We target `:host` directly with the resolved colour
    // tokens so EVERY descendant of the shadow root inherits them —
    // including position:fixed siblings of .root such as the picker
    // modal and the guide-cursor overlay. (An inline style on a
    // wrapper div would only cascade to its own subtree, which the
    // sibling-position:fixed picker can sit outside of in practice;
    // overriding `:host` removes that whole class of bug.)
    //
    // The widget's default `:host` block in styles.ts wins by source
    // order until this <style> is mounted; once mounted, it wins
    // because it appears LATER in the shadow root and selectors have
    // identical specificity.
    const themeCss = config?.themeColors
        ? `:host {
            --voqi-primary: ${config.themeColors.primary};
            --voqi-bg: ${config.themeColors.bg};
            --voqi-text: ${config.themeColors.text};
            --voqi-muted: ${config.themeColors.muted};
            --voqi-on-primary: ${config.themeColors.onPrimary};
        }`
        : "";

    const startVoice = async (language: string, mode: InAppMode) => {
        if (!config || transportRef.current?.isConnected()) return;
        setIsStarting(true);
        setConnectionPhase("connecting");
        sessionDeadRef.current = false;
        // Fresh attempt — clear the cancel flag so a previous
        // cancelled-mid-connect attempt doesn't leak its ref state
        // into this one.
        connectCancelledRef.current = false;
        // Fresh start — clear any prior session's transcript so the
        // visitor isn't looking at stale bubbles after a reconnect.
        setTranscript([]);
        // Every fresh session starts muted. The transport's
        // PipecatClient is also constructed with ``enableMic: false``
        // so the WebRTC layer never publishes the mic until the
        // visitor unmutes.
        setIsMuted(true);
        // Agent audio defaults to ON for every fresh session. The
        // visitor explicitly chose to talk to the agent — they
        // probably want to hear it on the first turn.
        setIsAgentMuted(false);
        setSessionEndingHint(null);
        try {
            const sess = await api.startSession({
                tools: mode === "guide" ? [] : registry.listSchemas(),
                language,
                mode,
            });
            sessionStartRef.current = new Date().toISOString();
            setSession(sess);

            const transport = transportFactory(registry, {
                onConnected: () => setAgentState("idle"),
                onBotReady: () => {
                    // The agent's pipeline is wired and the bot has
                    // greeted (or is about to). NOW we can tell the
                    // user "say something" without it being a lie.
                    setConnectionPhase("ready");
                    setAgentState("idle");
                },
                onDisconnected: () => {
                    // Don't override the trouble-state "error" with
                    // "idle" — when the trouble effect calls
                    // transport.disconnect(), Pipecat fires
                    // `disconnected` (intentional path), and without
                    // this guard the pill would flip back to idle/
                    // green while we still want it red + exclamation.
                    if (sessionDeadRef.current) return;
                    setAgentState("idle");
                },
                onUnexpectedDisconnect: () => {
                    setConnectionPhase("lost");
                    setAgentState("error");
                },
                onPermissionDenied: () => {
                    // Browser/OS refused mic access. Match the
                    // unexpected-disconnect path exactly: freeze the UI
                    // in the trouble state — exclamation pill, status
                    // copy "Microphone permission denied", every
                    // secondary control disabled, End remains the only
                    // action. Do NOT call endVoice here; that would
                    // null `session` and the widget would fall back to
                    // the launcher instead of showing the trouble UI.
                    // The visitor clicks End to actually tear down.
                    setConnectionPhase("permission_denied");
                    setAgentState("error");
                },
                onListening: () => {
                    if (sessionDeadRef.current) return;
                    setAgentState("listening");
                },
                onProcessing: () => {
                    if (sessionDeadRef.current) return;
                    setAgentState("processing");
                },
                // Tools-only mode — no TTS, so RTVI bot-speaking events
                // mark the *response window* (first token → end of grace
                // period) rather than literal audio playback. We surface
                // it to the user as "Responding…".
                onSpeaking: () => {
                    if (sessionDeadRef.current) return;
                    setAgentState("responding");
                },
                onIdle: () => {
                    if (sessionDeadRef.current) return;
                    // Only go idle if the assistant turn ended
                    // *naturally* — current state is "responding".
                    // If a user turn started DURING the assistant's
                    // response (interruption, voice or typed text),
                    // state is already "listening" or "processing"
                    // and the late assistant_turn_ended must NOT
                    // clobber it — otherwise the typed-text-during-
                    // response path ends at idle and stays there
                    // until the new assistant_turn_started lands.
                    setAgentState((current) =>
                        current === "responding" ? "idle" : current,
                    );
                },
                onAssistantTurnStarted: () => {
                    // No-op now. Earlier we pushed an empty placeholder
                    // bubble + a streaming caret on it, because Pipecat's
                    // RTVIEvent.BotTranscript fed us partial sentences and
                    // a "still being written" signal was needed. We now
                    // emit a single canonical assistant_message per LLM
                    // round (full text in one shot), so an empty bubble
                    // before content lands is just visual noise — the
                    // launcher pill flips to "Responding…" already.
                },
                onTranscript: (entry) => {
                    if (sessionDeadRef.current) return;
                    if (entry.role !== "assistant") {
                        // User: voice STT path. Tag with source="voice"
                        // so coalesceTranscript merges with a preceding
                        // voice bubble but NEVER merges with a typed-text
                        // bubble (typed text is a barrier).
                        const tagged: TranscriptEntry = { ...entry, source: "voice" };
                        setTranscript((prev) => coalesceTranscript(prev, tagged));
                        return;
                    }
                    // Each `assistant_message` is the full text of one
                    // LLM round in one shot — append it as its own
                    // bubble. Pipecat's LLMAssistantAggregator collapses
                    // a tool-call chain into a single assistant_turn,
                    // so several assistant_messages can land between
                    // one assistant_turn_started/ended pair; each
                    // becomes its own bubble.
                    setTranscript((prev) => prev.concat(entry));
                },
                onError: (_message) => {
                    setAgentState("error");
                },
                onSessionEnding: ({ reason, lingerSeconds }) => {
                    // Bot is closing the session intentionally. There
                    // are two flavours:
                    //   * Forced ends (idle timeout, 90-min cap): close
                    //     RIGHT NOW with no linger — there's nothing
                    //     for the visitor to read while the connection
                    //     dribbles out, just a "Session closed." hint
                    //     that auto-clears.
                    //   * Graceful end (the visitor confirmed, or any
                    //     bot-initiated farewell): use the bot's
                    //     suggested linger so the visitor sees the
                    //     last assistant message, then disconnect.
                    setConnectionPhase("ended");
                    setAgentState("idle");
                    // Only two reasons are emitted by the server:
                    // `idle_grace_elapsed` (forced — close immediately
                    // after the canned goodbye) and `visitor_confirmed_end`
                    // (graceful — linger so the visitor sees the bot's
                    // farewell sentence, then disconnect).
                    const isForced = reason === "idle_grace_elapsed";
                    setSessionEndingHint("Session ended.");
                    if (isForced) {
                        void endVoiceRef.current?.(false);
                    } else {
                        window.setTimeout(() => {
                            void endVoiceRef.current?.(false);
                        }, Math.max(500, lingerSeconds * 1000));
                    }
                },
                onGuideCursor: (hint) => {
                    if (sessionDeadRef.current) return;
                    // Replace any existing cursor — only one ever
                    // visible at a time. Reset the auto-fade timer to
                    // 20s so a fresh hint always gets the full window.
                    if (cursorTimerRef.current !== null) {
                        window.clearTimeout(cursorTimerRef.current);
                    }
                    setGuideCursor(hint);
                    cursorTimerRef.current = window.setTimeout(() => {
                        setGuideCursor(null);
                        cursorTimerRef.current = null;
                    }, 20_000);
                },
            });
            transportRef.current = transport;
            await transport.connect(sess);
            // Hand the underlying PipecatClient to React state AFTER
            // connect resolves — that's when the real transport has
            // actually constructed its PipecatClient. The audio
            // component will mount once this is non-null. Mock
            // transport returns null and the audio component is
            // skipped (no real audio in mock mode anyway).
            setPipecatClient(transport.getPipecatClient());
        } catch (err) {
            // Distinguish three outcomes so we don't swallow real
            // failures:
            //   1. User clicked End mid-connect → ``endVoice`` set
            //      ``connectCancelledRef.current = true``. Silent.
            //   2. The transport's poll loop reported its own
            //      "connect cancelled" message (visitor bailed during
            //      the agent-backend polling phase). Silent.
            //   3. Anything else — main backend errored on
            //      /in-app/widget/session, agent backend timed
            //      out after 5 minutes, network failure, etc. We
            //      never reached a working session, so this is
            //      "failed" (connection failed), NOT "lost"
            //      (connection lost). Surfacing the right wording
            //      matters: "lost" reads as "we were talking, then
            //      dropped"; "failed" reads as "we never got
            //      through". The pill copy + glyph differ accordingly.
            const message = err instanceof Error ? err.message : String(err);
            if (connectCancelledRef.current || message === "connect cancelled") {
                console.info("[Voqi] connect cancelled by user");
            } else {
                console.error("[Voqi] failed to start session", err);
                setAgentState("error");
                setConnectionPhase("failed");
            }
        } finally {
            setIsStarting(false);
        }
    };

    const toggleMute = () => {
        const next = !isMuted;
        setIsMuted(next);
        transportRef.current?.setMicrophoneMuted(next);
    };

    const toggleAgentMute = () => {
        setIsAgentMuted((v) => !v);
    };

    // Mute / unmute the bot's audio CLIENT-SIDE by walking every
    // <audio> element on the host page and toggling ``.muted``. The
    // ``<PipecatClientAudio />`` portal renders its <audio> as a
    // direct child of ``document.body``, so this querySelector
    // reaches it. Re-runs whenever ``isAgentMuted`` flips OR
    // ``pipecatClient`` becomes non-null (i.e. a fresh session
    // re-mounts the audio element). Track-level disable lives in
    // ``BotAudioController`` for defense-in-depth.
    useEffect(() => {
        document.querySelectorAll("audio").forEach((el) => {
            el.muted = isAgentMuted;
        });
    }, [isAgentMuted, pipecatClient]);

    const submitText = () => {
        const text = pendingText.trim();
        if (!text || !transportRef.current) return;
        // Echo into the local transcript right away — typed messages
        // don't come back as a UserTranscript event from STT, so
        // without this the user's bubble would never appear. Tagged
        // source="text" so coalesceTranscript treats it as a barrier:
        // it never extends a previous bubble and a later user message
        // (voice or text) never extends it. Each typed submission is
        // an explicit, self-contained event.
        const entry: TranscriptEntry = {
            role: "user",
            content: text,
            timestamp: new Date().toISOString(),
            source: "text",
        };
        setTranscript((prev) => coalesceTranscript(prev, entry));
        transportRef.current.sendTextMessage(text);
        setPendingText("");
    };

    const endVoice = async (closePanel: boolean) => {
        const transport = transportRef.current;
        transportRef.current = null;
        // Tell any in-flight ``startVoice`` that this is a user-
        // initiated cancellation — its catch path uses this to
        // suppress the "Connection lost" surface (the user CHOSE to
        // bail; that's not a failure).
        connectCancelledRef.current = true;
        setSession(null);
        sessionStartRef.current = null;
        // User clicked End — wipe the trouble state. Whether we were
        // in "lost", "permission_denied", or a normal active session,
        // End means "I'm done, clear everything so the launcher shows
        // fresh." Otherwise the next mount of the launcher pill would
        // still render red with the exclamation, and the next Start
        // would inherit a corpse state.
        setConnectionPhase("ended");
        setAgentState("idle");
        // Reset to muted so the next session starts in the same
        // muted state, matching the initial useState default and the
        // PipecatClient's ``enableMic: false``.
        setIsMuted(true);
        // Agent audio resets to ON for the next session.
        setIsAgentMuted(false);
        // Drop the client reference so the next session re-mounts
        // ``<PipecatClientAudio />`` against a fresh PipecatClient
        // (a new one gets constructed inside ``transportFactory``
        // for every startVoice).
        setPipecatClient(null);
        setTextInputMode(false);
        setPendingText("");
        // Belt-and-suspenders: clear every cross-session-leakable bit
        // EXCEPT ``sessionEndingHint``. The cap-end / idle-end paths
        // set the hint moments before calling endVoice (so the
        // visitor sees WHY the session closed); clearing it here
        // would be a setState race with that prior set, and the
        // visitor would never see the message before the launcher
        // pill returns. The hint cleans itself up after
        // ENDING_HINT_AUTO_CLEAR_MS (~10s) and startVoice clears it
        // when the next session begins, so leaving it set here is
        // safe and is the only way the cap-end notice is visible.
        setIsStarting(false);
        setTranscript([]);
        sessionDeadRef.current = false;
        // Clear any guide-mode cursor + its auto-fade timer so the
        // next session starts with a clean overlay state.
        if (cursorTimerRef.current !== null) {
            window.clearTimeout(cursorTimerRef.current);
            cursorTimerRef.current = null;
        }
        setGuideCursor(null);

        if (transport) {
            try {
                await transport.disconnect();
            } catch (err) {
                console.warn("[Voqi] disconnect failed", err);
            }
        }

        // Session analytics (transcript, tool-call log, durations,
        // counters) are now POSTed by the bot at end-of-session via
        // the same InAppSessionAccessGuard endpoint
        // (`POST /in-app/widget/session/:uuid/complete`). The
        // widget no longer carries the analytics surface — that data
        // can survive a tabclose / network drop on the visitor's side
        // because the bot owns the lifecycle write.

        setAgentState("idle");
        if (closePanel) onCloseRequest();
    };
    endVoiceRef.current = endVoice;

    // Cleanup on unmount.
    useEffect(() => {
        return () => {
            void endVoice(false);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // When the session lands in a "trouble" state (connection lost OR
    // agent error), collapse every secondary UI surface so the only
    // affordance left is End. Closing the panel + text input here is
    // belt-and-braces: the controls themselves are also disabled in
    // trouble, but if the visitor had any of them open at the moment
    // the error hit, those panels would otherwise stay rendered with
    // stale content.
    useEffect(() => {
        const inTrouble =
            connectionPhase === "lost" ||
            connectionPhase === "failed" ||
            connectionPhase === "permission_denied" ||
            agentState === "error";
        // Flip the synchronous "session is dead" ref before any of the
        // other reactive cleanup runs, so transport handlers firing on
        // this same tick (or later) drop their payloads instead of
        // mutating state on a corpse.
        sessionDeadRef.current = inTrouble;
        if (!inTrouble) return;
        // Leave the Daily room IMMEDIATELY on any terminal error —
        // don't wait for the visitor to click End. The backend's
        // InAppAgentProcessor watches for the WebRTC disconnect
        // and tears down the bot worker, the Daily room, the LLM
        // pipeline, etc. as soon as we drop. Leaving it alive until
        // the visitor manually ends would burn server resources for
        // up to 60s (heartbeat timeout) on every error. Idempotent:
        // unexpected-disconnect path already disconnected the client,
        // permission-denied path needs us to do it now.
        const t = transportRef.current;
        if (t?.isConnected()) {
            void t.disconnect();
        }
        if (textInputMode) {
            setTextInputMode(false);
            setPendingText("");
        }
        if (isOpen) onCloseRequest();
    }, [connectionPhase, agentState, isOpen, textInputMode, onCloseRequest]);

    // Pill visual state. We collapsed the palette: the only colour
    // changes the visitor sees are the brand primary (idle / live /
    // working / responding) and red (error / lost). Listening uses the
    // accent's status-dot but keeps the dark chrome.
    const pillState: "idle" | "connecting" | "live" | "listening" | "working" | "error" =
        connectionPhase === "lost" ||
        connectionPhase === "failed" ||
        connectionPhase === "permission_denied"
            ? "error"
            : connectionPhase === "connecting"
              ? "connecting"
              : !session
                ? "idle"
                : agentState === "listening"
                  ? "listening"
                  : agentState === "processing" || agentState === "responding"
                    ? "working"
                    : agentState === "error"
                      ? "error"
                      : "live";

    // Status text reflects ONLY the agent's current state. Mute is
    // shown via the mic-icon button's pressed state, not in copy.
    const statusLabel = (() => {
        if (connectionPhase === "connecting") return "Connecting…";
        if (connectionPhase === "failed") return "Connection failed";
        if (connectionPhase === "lost") return "Connection lost";
        if (connectionPhase === "permission_denied")
            return "Microphone permission denied";
        if (!session) return "Tap to talk";
        switch (agentState) {
            case "listening":
                return "Listening…";
            case "processing":
                return "Thinking…";
            case "responding":
                return "Responding…";
            case "error":
                return "Connection issue";
            default:
                return "Ready";
        }
    })();

    // Every assistant turn the visitor has heard, oldest-first. The
    // caption stack IS the conversation history — there's no separate
    // popover anymore. We page through pairs of two: the newest pair
    // shows by default, scrolling up or pressing the chevron buttons
    // pages backward through older pairs. Both user and assistant
    // turns appear here — visitors need to see their utterance echoed
    // back so they can verify STT got it right (the only signal they
    // have that the agent actually heard them). The caption styling
    // distinguishes by role (data-role).
    const allTurns = transcript;

    // pairIndex is now a MESSAGE offset, not a pair offset — the
    // chevron buttons advance one message at a time, but the visible
    // window still shows up to 2 messages (latest + prior). 0 =
    // newest message at the bottom; 1 = newest is the second-newest
    // message; etc. We INTENTIONALLY do NOT auto-reset to 0 when a
    // new turn arrives — yanking the visitor back to "newest" while
    // they're reading older history would be jarring.
    const [pairIndex, setPairIndex] = useState(0);
    const maxPairIndex = Math.max(0, allTurns.length - 2);
    const visiblePair = useMemo<TranscriptEntry[]>(() => {
        const len = allTurns.length;
        if (len === 0) return [];
        const end = len - pairIndex;
        const start = Math.max(0, end - 2);
        return allTurns.slice(start, end);
    }, [allTurns, pairIndex]);
    // Defensive clamp: if pairIndex was at the end and the dataset
    // shrank (history hard-cap eviction), or the visitor disconnected
    // and reconnected, keep pairIndex within range.
    useEffect(() => {
        if (pairIndex > maxPairIndex) {
            setPairIndex(maxPairIndex);
        }
    }, [pairIndex, maxPairIndex]);

    // The chevron on the pill toggles "lock open". When isOpen=true
    // the stack stays visible regardless of the linger timer; when
    // isOpen=false the stack auto-fades once the agent stops talking.
    // Either way the stack content + paging behaviour are identical.
    const historyLocked = isOpen && session !== null && allTurns.length > 0;

    // Caption stack auto-fade. Only kicks in when (a) the agent has
    // fully stopped speaking, (b) the history isn't locked open, and
    // (c) the visitor isn't actively interacting with the stack
    // (hovering pauses the timer).
    const [captionFadedOut, setCaptionFadedOut] = useState(false);
    const [captionHovered, setCaptionHovered] = useState(false);
    const prevAssistantCountRef = useRef(0);
    useEffect(() => {
        // Un-fade ONLY when a new assistant turn just arrived (length
        // grew — placeholder pushed by onAssistantTurnStarted, or a
        // brand-new entry) or when the agent is actively responding.
        // We do NOT un-fade just because the agent state flipped to
        // "processing" (which happens the moment the visitor sends
        // a text — there's no reply yet, so the history pop-up
        // shouldn't open in anticipation; it should wait until the
        // agent's placeholder bubble lands).
        //
        // The auto-open is GATED on ``isAgentMuted``. When the agent
        // is unmuted (audio plays), the visitor hears the reply —
        // popping the caption stack open is redundant and competes
        // with whatever the visitor was reading on the host page.
        // When the agent is muted (or audio failed), captions are
        // the visitor's only signal that a reply landed, so the
        // auto-open is essential. If the visitor manually opens via
        // the chevron (``historyLocked``), this gate doesn't apply
        // — they explicitly want to see captions.
        const grew = allTurns.length > prevAssistantCountRef.current;
        prevAssistantCountRef.current = allTurns.length;
        if ((grew || agentState === "responding") && isAgentMuted) {
            setCaptionFadedOut(false);
        }
        if (agentState === "responding") return;
        if (allTurns.length === 0) return;
        if (historyLocked) return;
        if (captionHovered) return;
        const t = window.setTimeout(
            () => setCaptionFadedOut(true),
            CAPTION_LINGER_MS,
        );
        return () => window.clearTimeout(t);
    }, [allTurns, agentState, historyLocked, captionHovered, isAgentMuted]);

    // Caption stack visibility. Either the visitor explicitly locked
    // it open, or the agent has produced something to show and the
    // linger timer hasn't fired yet. Trouble states collapse the
    // widget to "status text + End" only — no captions floating during
    // a connection loss.
    const captionVisible =
        !!session &&
        connectionPhase !== "lost" &&
        connectionPhase !== "permission_denied" &&
        agentState !== "error" &&
        allTurns.length > 0 &&
        (historyLocked || !captionFadedOut);

    // Whenever the history surface goes from hidden to visible — visitor
    // pressed the chevron to lock it open, OR an auto-show fires after
    // a dismissed/faded period — snap to the newest pair so they always
    // see the latest reply on (re)appearance. While the surface STAYS
    // visible, paging back via the Earlier/Newer buttons preserves the
    // visitor's chosen pairIndex (a new turn landing mid-read does NOT
    // yank them down).
    const prevCaptionVisibleRef = useRef(false);
    useEffect(() => {
        if (captionVisible && !prevCaptionVisibleRef.current) {
            setPairIndex(0);
        }
        prevCaptionVisibleRef.current = captionVisible;
    }, [captionVisible]);

    // Picker eligibility:
    //   * Language picker is ALWAYS shown — the full hardcoded list,
    //     regardless of which voices the agent profile has configured
    //     server-side. The bot handles fall-back voices for languages
    //     the profile doesn't have a dedicated TTS voice for.
    //   * Mode picker only worth showing when the host registered any
    //     tools at all. Zero tools → there's nothing to "act on", so
    //     guide mode is the only sensible mode and we skip the step.
    const hostTools = registry.listSchemas();
    const needsModePicker = hostTools.length > 0;
    const defaultLanguage = detectBrowserLanguage();
    const fallbackMode: InAppMode =
        hostTools.length > 0 ? "action" : "guide";

    // The launcher button is ONLY "start". There is no separate
    // reconnect state — after the visitor ends a lost session, the
    // pill goes back to the same "Talk to assistant" launcher. Less
    // state, fewer copy variants. While in-session this branch isn't
    // rendered; the End button on the pill does the closing.
    const primaryAction = () => {
        if (connectionPhase === "connecting") return;
        if (session) return;
        if (!config) return;
        // Reset any stale lost/error flags so the new session starts clean.
        if (connectionPhase !== "fresh") setConnectionPhase("fresh");
        // Language picker is always shown (we mirror the demo's full
        // 37-language list), so we always open the modal — even on
        // a no-tool agent where the visitor's only real decision is
        // language. Pre-seed the language with the visitor's auto-
        // detected default so they only have to confirm or change it.
        // default — opens the picker showing that language already
        // selected, the visitor only has to confirm or change it.
        setPickedLanguage(defaultLanguage);
        setLanguageDropdownOpen(false);
        setPickerOpen(true);
    };

    const onModePicked = (mode: InAppMode) => {
        setPickerOpen(false);
        setLanguageDropdownOpen(false);
        void startVoice(pickedLanguage, mode);
    };

    const onPickerDismiss = () => {
        // Cancel out of the picker without starting a session. Leave
        // ``pickedLanguage`` as-is — re-opening on the next click
        // remembers the visitor's last choice without re-running
        // browser-language detection.
        setPickerOpen(false);
        setLanguageDropdownOpen(false);
    };

    // When there's only one mode (no host tools), the modal is
    // essentially "confirm your language and start" — no mode cards
    // to pick. We use a single big Start button in that case.
    const onStartFromPicker = () => {
        setPickerOpen(false);
        setLanguageDropdownOpen(false);
        void startVoice(pickedLanguage, fallbackMode);
    };

    const primaryAriaLabel =
        connectionPhase === "connecting" ? "Connecting" : "Start voice session";
    const primaryHoverHint =
        connectionPhase === "connecting"
            ? "Connecting…"
            : "Click to talk to your assistant";

    // Minimize tucks the widget DOWN off-screen — anchored to the same
    // bottom corner the widget normally sits in. The in-pill chevron
    // points down (tuck) and the restore tab's chevron points up
    // (bring back up). Bottom anchor gives the restore tab horizontal
    // room for an actual text label, which is far more discoverable
    // than a chevron sliver pinned to a side edge.

    // If the boot fetch (`GET /in-app/widget/config`) errored,
    // render NOTHING. The visitor hasn't interacted with anything
    // yet — a sudden "Connection failed" pill appearing on a page
    // they didn't know had an assistant would be confusing. The
    // console.error above is the signal for the host integrator
    // who's debugging the embed; visitors just see no widget.
    if (configError !== null) return null;

    return (
        <>
        {/* Theme override stylesheet — written into the shadow root so
            `:host` overrides cascade to every descendant (including the
            position:fixed picker modal). Empty string when no theme is
            set; the widget's built-in defaults from styles.ts apply. */}
        {themeCss && <style>{themeCss}</style>}
        {/* PipecatClientAudio creates the <audio> element that
            actually plays the bot's inbound audio track. We render
            it via a portal into document.body — i.e. OUTSIDE the
            widget's Shadow DOM — so:
              1. ``document.querySelectorAll("audio")`` in the
                 mute-effect actually finds and toggles it.
              2. The audio element lives at the host page's top
                 level where browsers handle WebRTC playback the
                 most reliably.
            Skipped in mock mode (PipecatClient is null there). */}
        {pipecatClient !== null
            ? createPortal(
                  <PipecatClientProvider client={pipecatClient}>
                      <PipecatClientAudio />
                      <BotAudioController isMuted={isAgentMuted} />
                  </PipecatClientProvider>,
                  document.body,
              )
            : null}
        <div
            className="root"
            data-position={position}
            data-pill-state={pillState}
            data-minimized={isMinimized ? "true" : "false"}
            aria-hidden={isMinimized ? "true" : undefined}
        >
            {captionVisible && (
                <div
                    className="caption-stack"
                    data-locked={historyLocked ? "true" : "false"}
                    data-pageable={maxPairIndex > 0 ? "true" : "false"}
                    role="status"
                    aria-live="polite"
                    onMouseEnter={() => setCaptionHovered(true)}
                    onMouseLeave={() => setCaptionHovered(false)}
                >
                    {pairIndex < maxPairIndex && (
                        <button
                            type="button"
                            className="caption-pager caption-pager-up"
                            aria-label="Show earlier replies"
                            onClick={() =>
                                setPairIndex((p) =>
                                    Math.min(maxPairIndex, p + 1),
                                )
                            }
                        >
                            <svg
                                width="12"
                                height="12"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2.6"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                aria-hidden="true"
                            >
                                <polyline points="6 14 12 8 18 14" />
                            </svg>
                            <span>Earlier</span>
                        </button>
                    )}
                    <div className="caption-stack-pair">
                        {visiblePair.map((entry, i) => {
                            // The bottom caption of the visible pair is
                            // the "latest" — full opacity. The one above
                            // is "prior" — faded. Same shape regardless
                            // of which pair you're looking at.
                            const recency =
                                i === visiblePair.length - 1
                                    ? "latest"
                                    : "prior";
                            return (
                                <div
                                    key={`${entry.timestamp}-${i}`}
                                    className="caption"
                                    data-role={entry.role}
                                    data-recency={recency}
                                >
                                    {entry.content}
                                </div>
                            );
                        })}
                    </div>
                    {pairIndex > 0 && (
                        <button
                            type="button"
                            className="caption-pager caption-pager-down"
                            aria-label="Show newer replies"
                            onClick={() =>
                                setPairIndex((p) => Math.max(0, p - 1))
                            }
                        >
                            <span>Newer</span>
                            <svg
                                width="12"
                                height="12"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2.6"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                aria-hidden="true"
                            >
                                <polyline points="6 10 12 16 18 10" />
                            </svg>
                        </button>
                    )}
                </div>
            )}

            {/* Connection-lost / agent-error / generic backend errors are
                NOT surfaced as toasts. The pill's status text already
                says "Connection lost" / "Connection issue", and we disable
                every secondary control except End so the visitor's only
                action is to wrap up and (optionally) restart. Less copy,
                less noise. */}

            {sessionEndingHint !== null && (
                <div className="toast toast-warn" role="status">
                    {sessionEndingHint}
                </div>
            )}

            {textInputMode && session && connectionPhase === "ready" && agentState !== "error" && (
                <div className="text-input-row" role="group" aria-label="Type a message">
                    <input
                        type="text"
                        className="text-input"
                        placeholder="Type a message…"
                        value={pendingText}
                        onChange={(e) => setPendingText(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                                e.preventDefault();
                                submitText();
                            }
                            if (e.key === "Escape") {
                                setTextInputMode(false);
                                setPendingText("");
                            }
                        }}
                        autoFocus
                    />
                    <button
                        type="button"
                        className="text-send"
                        onClick={submitText}
                        disabled={!pendingText.trim()}
                        aria-label="Send"
                    >
                        <SendGlyph />
                    </button>
                </div>
            )}

            <div
                className="pill"
                data-state={pillState}
                data-shape={launcherShape}
                role="toolbar"
                aria-label="Voice assistant controls"
            >
                {/* Three pill modes:
                      1. Session active OR connecting OR pre-session
                         trouble (lost / permission_denied / error) →
                         status text on the left + End/Cancel button
                         on the right. Same UI shape across all of
                         these so the visitor always sees ONE
                         consistent "we're in a session-y state, here's
                         the status, click End to bail" affordance.
                      2. Fresh / ended → clickable launcher button. */}
                {(() => {
                    const preSessionTrouble =
                        !session &&
                        (connectionPhase === "lost" ||
                            connectionPhase === "failed" ||
                            connectionPhase === "permission_denied" ||
                            agentState === "error");
                    const showStatusPill =
                        session !== null ||
                        connectionPhase === "connecting" ||
                        preSessionTrouble;
                    if (!showStatusPill) {
                        return (
                            <button
                                type="button"
                                className="pill-action"
                                onClick={primaryAction}
                                aria-label={primaryAriaLabel}
                                disabled={isStarting}
                                data-tooltip={primaryHoverHint}
                            >
                                <span className="pill-indicator" aria-hidden>
                                    <PillIndicator state={pillState} />
                                </span>
                                {launcherShape !== "circle" && (
                                    <span className="pill-label">
                                        Talk to assistant
                                    </span>
                                )}
                            </button>
                        );
                    }
                    return (
                        <>
                            <div
                                className="pill-status"
                                role="status"
                                aria-live="polite"
                            >
                                <span className="pill-indicator" aria-hidden>
                                    <PillIndicator state={pillState} />
                                </span>
                                {launcherShape !== "circle" && (
                                    <span className="pill-status-text">
                                        {statusLabel}
                                    </span>
                                )}
                            </div>
                            {!session && (
                                /* Pre-session connecting OR pre-
                                   session trouble: no in-session
                                   controls below, so we render the
                                   End/Cancel button here. Same shape
                                   either way so the visitor's escape
                                   hatch is consistent. */
                                <button
                                    type="button"
                                    className="pill-secondary pill-end"
                                    aria-label={
                                        connectionPhase === "connecting"
                                            ? "Cancel"
                                            : "End session"
                                    }
                                    onClick={() => endVoice(false)}
                                    data-tooltip={
                                        connectionPhase === "connecting"
                                            ? "Cancel"
                                            : "End"
                                    }
                                >
                                    <EndGlyph />
                                </button>
                            )}
                        </>
                    );
                })()}

                {/* In-session controls. When the connection is lost OR the
                    agent is in an error state, every secondary control is
                    disabled — End is the only sensible action left. The
                    visitor ends, then optionally clicks the launcher to
                    start a fresh session. No popup, no toast, no banner —
                    the status text already says what's wrong. */}
                {session && (() => {
                    const inTrouble =
                        connectionPhase === "lost" ||
                        connectionPhase === "permission_denied" ||
                        agentState === "error";
                    return (
                        <>
                            <button
                                type="button"
                                className="pill-secondary"
                                aria-label={isMuted ? "Unmute mic" : "Mute mic"}
                                aria-pressed={isMuted}
                                onClick={toggleMute}
                                disabled={inTrouble}
                                data-tooltip={isMuted ? "Unmute" : "Mute"}
                            >
                                <MicGlyph muted={isMuted} />
                            </button>
                            <button
                                type="button"
                                className="pill-secondary"
                                aria-label={
                                    isAgentMuted
                                        ? "Unmute agent audio"
                                        : "Mute agent audio"
                                }
                                aria-pressed={isAgentMuted}
                                onClick={toggleAgentMute}
                                disabled={inTrouble}
                                data-tooltip={
                                    isAgentMuted
                                        ? "Unmute agent"
                                        : "Mute agent"
                                }
                            >
                                <SpeakerGlyph muted={isAgentMuted} />
                            </button>
                            <button
                                type="button"
                                className="pill-secondary"
                                aria-label={textInputMode ? "Switch to voice" : "Type a message"}
                                aria-pressed={textInputMode}
                                onClick={() => {
                                    setTextInputMode((v) => !v);
                                    setPendingText("");
                                }}
                                disabled={inTrouble || connectionPhase !== "ready"}
                                data-tooltip={textInputMode ? "Hide keyboard" : "Type a message"}
                            >
                                <KeyboardGlyph />
                            </button>
                            <button
                                type="button"
                                className="pill-secondary"
                                aria-label={
                                    captionVisible
                                        ? "Hide conversation history"
                                        : "Show conversation history"
                                }
                                aria-pressed={captionVisible}
                                onClick={() => {
                                    // Sync the visual state with the
                                    // visitor's intent. If history is
                                    // currently visible (auto-shown OR
                                    // locked), dismiss it: kill the
                                    // linger AND drop the lock. If it
                                    // isn't visible, lock it open.
                                    if (captionVisible) {
                                        setCaptionFadedOut(true);
                                        if (isOpen) onCloseRequest();
                                    } else {
                                        if (!isOpen) onCloseRequest();
                                    }
                                }}
                                data-tooltip={
                                    captionVisible ? "Hide history" : "Show history"
                                }
                                disabled={inTrouble || allTurns.length === 0}
                            >
                                <TranscriptGlyph open={captionVisible} />
                            </button>
                            <span className="pill-divider" aria-hidden />
                            <button
                                type="button"
                                className="pill-secondary pill-end"
                                aria-label="End session"
                                onClick={() => endVoice(false)}
                                data-tooltip="End session"
                            >
                                <EndGlyph />
                            </button>
                        </>
                    );
                })()}

                {/* Minimize button — DOM-last so it always sits on the
                    right edge of the pill regardless of position
                    anchor. Tucks the whole widget shell off-screen
                    toward whichever side .root anchors to. Always
                    present, both in-session and on the launcher pill,
                    so the visitor can always get the widget out of the
                    way. */}
                <button
                    type="button"
                    className="pill-minimize"
                    aria-label="Minimize widget"
                    onClick={() => setIsMinimized(true)}
                    data-tooltip="Minimize"
                >
                    <ChevronGlyph direction="down" />
                </button>
            </div>
        </div>
        {/* Pre-session picker modal. Two-step (language → mode), with
            either step skipped when there's no real choice (one
            language configured, or zero host tools so guide mode is
            the only option). The modal lives in the Shadow DOM so
            host-page CSS can't restyle it and host-page z-indexes
            can't cover it. Backdrop click + Escape cancel without
            starting a session. */}
        {pickerOpen && (
            <div
                className="picker-backdrop"
                role="dialog"
                aria-modal="true"
                aria-label="Start the assistant"
                onClick={onPickerDismiss}
                onKeyDown={(e) => {
                    if (e.key === "Escape") onPickerDismiss();
                }}
            >
                <div
                    className="picker-card"
                    onClick={(e) => e.stopPropagation()}
                >
                    <button
                        type="button"
                        className="picker-close"
                        aria-label="Cancel"
                        onClick={onPickerDismiss}
                    >
                        <svg
                            width="14"
                            height="14"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2.4"
                            strokeLinecap="round"
                            aria-hidden="true"
                        >
                            <line x1="6" y1="6" x2="18" y2="18" />
                            <line x1="6" y1="18" x2="18" y2="6" />
                        </svg>
                    </button>
                    {/* Header — title on the left, compact language
                        pill dropdown on the right (globe + flag + ISO
                        code, click to expand the menu). */}
                    <div className="picker-header">
                        <h2
                            className="picker-title picker-defocusable"
                            data-defocused={
                                languageDropdownOpen ? "true" : "false"
                            }
                        >
                            {needsModePicker
                                ? "How would you like to use the assistant?"
                                : "Start the assistant"}
                        </h2>
                        <div className="picker-language">
                            <button
                                type="button"
                                className="picker-language-pill"
                                aria-haspopup="listbox"
                                aria-expanded={languageDropdownOpen}
                                onClick={() =>
                                    setLanguageDropdownOpen((v) => {
                                        if (v) setLanguageSearch("");
                                        return !v;
                                    })
                                }
                            >
                                <svg
                                    width="13"
                                    height="13"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    aria-hidden="true"
                                    className="picker-language-globe"
                                >
                                    <circle cx="12" cy="12" r="10" />
                                    <line x1="2" y1="12" x2="22" y2="12" />
                                    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                                </svg>
                                <span className="picker-language-code">
                                    {(
                                        LANGUAGE_OPTIONS.find(
                                            (o) => o.value === pickedLanguage,
                                        )?.label ?? pickedLanguage
                                    ).toUpperCase()}
                                </span>
                                <svg
                                    width="11"
                                    height="11"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2.4"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    aria-hidden="true"
                                    data-open={
                                        languageDropdownOpen
                                            ? "true"
                                            : "false"
                                    }
                                    className="picker-language-chevron"
                                >
                                    <polyline points="6 9 12 15 18 9" />
                                </svg>
                            </button>
                            {languageDropdownOpen && (() => {
                                const q = languageSearch.trim().toLowerCase();
                                const filtered = q
                                    ? LANGUAGE_OPTIONS.filter(
                                          (opt) =>
                                              opt.label
                                                  .toLowerCase()
                                                  .startsWith(q) ||
                                              opt.value.startsWith(q),
                                      )
                                    : LANGUAGE_OPTIONS;
                                return (
                                    <div className="picker-language-menu">
                                        <input
                                            type="text"
                                            className="picker-language-search"
                                            placeholder="Search…"
                                            value={languageSearch}
                                            autoFocus
                                            onChange={(e) =>
                                                setLanguageSearch(
                                                    e.currentTarget.value,
                                                )
                                            }
                                            onKeyDown={(e) => {
                                                if (e.key === "Escape") {
                                                    setLanguageDropdownOpen(
                                                        false,
                                                    );
                                                    setLanguageSearch("");
                                                } else if (
                                                    e.key === "Enter" &&
                                                    filtered.length > 0
                                                ) {
                                                    setPickedLanguage(
                                                        filtered[0].value,
                                                    );
                                                    setLanguageDropdownOpen(
                                                        false,
                                                    );
                                                    setLanguageSearch("");
                                                }
                                            }}
                                        />
                                        {filtered.length === 0 ? (
                                            <div className="picker-language-empty">
                                                No matches
                                            </div>
                                        ) : (
                                            <ul
                                                className="picker-language-list"
                                                role="listbox"
                                            >
                                                {filtered.map((opt) => (
                                                    <li key={opt.value}>
                                                        <button
                                                            type="button"
                                                            role="option"
                                                            aria-selected={
                                                                pickedLanguage ===
                                                                opt.value
                                                            }
                                                            className="picker-language-option"
                                                            data-active={
                                                                pickedLanguage ===
                                                                opt.value
                                                                    ? "true"
                                                                    : "false"
                                                            }
                                                            onClick={() => {
                                                                setPickedLanguage(
                                                                    opt.value,
                                                                );
                                                                setLanguageDropdownOpen(
                                                                    false,
                                                                );
                                                                setLanguageSearch(
                                                                    "",
                                                                );
                                                            }}
                                                        >
                                                            <span className="picker-language-option-flag">
                                                                {opt.flag}
                                                            </span>
                                                            <span className="picker-language-option-name">
                                                                {opt.label}
                                                            </span>
                                                        </button>
                                                    </li>
                                                ))}
                                            </ul>
                                        )}
                                    </div>
                                );
                            })()}
                        </div>
                    </div>
                    <div
                        className="picker-defocusable"
                        data-defocused={
                            languageDropdownOpen ? "true" : "false"
                        }
                    >
                        {needsModePicker ? (
                            <div className="picker-options picker-options-stack">
                                <button
                                    type="button"
                                    className="picker-option-card"
                                    onClick={() => onModePicked("action")}
                                >
                                    <span className="picker-option-card-title">
                                        Act for me
                                    </span>
                                    <span className="picker-option-card-body">
                                        The assistant can perform actions
                                        in the app on your behalf —
                                        clicking, filling out forms, and
                                        finishing tasks for you. Best when
                                        you want it to do the work.
                                    </span>
                                </button>
                                <button
                                    type="button"
                                    className="picker-option-card"
                                    onClick={() => onModePicked("guide")}
                                >
                                    <span className="picker-option-card-title">
                                        Guide me
                                    </span>
                                    <span className="picker-option-card-body">
                                        The assistant explains what to do
                                        and points to where to click — you
                                        do the steps yourself. It cannot
                                        take actions in this mode. Best
                                        for learning the product.
                                    </span>
                                </button>
                            </div>
                        ) : (
                            <button
                                type="button"
                                className="picker-start"
                                onClick={onStartFromPicker}
                            >
                                Start
                            </button>
                        )}
                    </div>
                </div>
            </div>
        )}
        {/* Bottom-anchored restore tab. Only rendered when minimized.
            Lives flush against the bottom of the viewport in the same
            corner the widget anchors to (bottom-right or bottom-left);
            the up-chevron + "Show assistant" label tell the visitor
            exactly what clicking it will do. Bottom edge gives us the
            horizontal space for a real label — way more discoverable
            than a chevron sliver pinned to a side edge. */}
        {isMinimized && (
            <button
                type="button"
                className="restore-tab"
                data-position={position}
                onClick={() => setIsMinimized(false)}
                aria-label="Show assistant"
            >
                <ChevronGlyph direction="up" />
                <span className="restore-tab-label">Show assistant</span>
            </button>
        )}
        {/* Guide-mode ghost cursor overlay. Sibling of .root (NOT a
            child) so the minimize transform doesn't drag it off
            screen along with the rest of the widget. ``position:
            fixed`` puts it directly at the viewport coordinate the
            bot computed (normalized × current viewport). The dot is
            non-interactive — pointer-events: none so it never blocks
            the visitor's actual click. ``aria-hidden`` because the
            spoken response carries the same instruction; this is a
            visual hint only. */}
        {guideCursor && (
            <div
                className="guide-cursor"
                style={{
                    left: `${guideCursor.x * 100}%`,
                    top: `${guideCursor.y * 100}%`,
                }}
                role="img"
                aria-label={
                    guideCursor.label
                        ? `Agent: ${guideCursor.label}`
                        : "Agent cursor"
                }
            >
                <svg
                    className="guide-cursor-pointer"
                    width="20"
                    height="24"
                    viewBox="-1 -1 20 24"
                    aria-hidden="true"
                >
                    {/* Classic mouse-pointer arrow. Path starts at
                        (0, 0) so the cursor's TIP lands exactly on
                        the wrapper origin — i.e. exactly on the
                        coordinate the bot picked. The viewBox is
                        nudged to (-1, -1) so the 1.5px outer stroke
                        doesn't get clipped at the tip. */}
                    <path
                        d="M0 0 L0 20 L5.2 15.2 L8.6 22.2 L11.6 20.8 L8.2 13.8 L15 13.8 Z"
                        fill="#3B82F6"
                        stroke="#0A0A0A"
                        strokeWidth="1.5"
                        strokeLinejoin="round"
                    />
                </svg>
                <div className="guide-cursor-tag">
                    <span className="guide-cursor-tag-text">Agent</span>
                </div>
            </div>
        )}
        </>
    );
}

/**
 * Merge two consecutive user transcript entries into one bubble —
 * regardless of timing — UNLESS either side is a typed-text bubble.
 * Typed-text bubbles (``source === "text"``) are barriers: they never
 * extend a previous bubble and a later user message never extends
 * them. Voice STT chunks (``source === "voice"`` or undefined) merge
 * with each other so consecutive STT fragments render as one
 * utterance. Assistant entries don't go through here — each
 * ``assistant_message`` server message is its own bubble appended
 * directly by the caller.
 */
function coalesceTranscript(
    prev: TranscriptEntry[],
    next: TranscriptEntry,
): TranscriptEntry[] {
    const last = prev[prev.length - 1];
    const bothUser =
        next.role === "user" && last?.role === "user";
    const eitherIsTyped =
        next.source === "text" || last?.source === "text";
    if (bothUser && !eitherIsTyped) {
        const merged: TranscriptEntry = {
            role: last.role,
            content: `${last.content} ${next.content}`.trim(),
            timestamp: last.timestamp,
            source: last.source,
        };
        const out = prev.slice(0, -1).concat(merged);
        return out.length > TRANSCRIPT_HARD_CAP
            ? out.slice(out.length - TRANSCRIPT_HARD_CAP)
            : out;
    }
    const out = prev.concat(next);
    return out.length > TRANSCRIPT_HARD_CAP
        ? out.slice(out.length - TRANSCRIPT_HARD_CAP)
        : out;
}

function SpeakerGlyph({ muted }: { muted: boolean }) {
    // Speaker icon for the agent-audio mute. ``muted`` overlays a
    // small "x" near the speaker waves so the on/off state reads at
    // a glance, mirroring MicGlyph's pattern.
    return (
        <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
        >
            <path d="M11 5 L6 9 H2 v6 h4 l5 4 z" />
            {muted ? (
                <>
                    <line x1="16" y1="9" x2="22" y2="15" />
                    <line x1="22" y1="9" x2="16" y2="15" />
                </>
            ) : (
                <>
                    <path d="M15.54 8.46 a5 5 0 0 1 0 7.07" />
                    <path d="M19.07 4.93 a10 10 0 0 1 0 14.14" />
                </>
            )}
        </svg>
    );
}

/**
 * Tiny React component that lives inside the ``<PipecatClientProvider>``
 * (rendered alongside ``<PipecatClientAudio />`` via the same portal).
 * Its only job is to disable the bot's inbound audio track at the
 * WebRTC level when ``isMuted`` is true — defense-in-depth on top of
 * the ``audioElement.muted`` toggle.
 *
 * Why both: the audio-element mute is what the visitor's browser
 * uses to silence playback, but if for any reason the element isn't
 * found (Shadow-DOM edge cases, race with track-publish, browser
 * quirks), disabling the track stops the bytes at the data layer.
 * Belt-and-braces.
 *
 * Returns null — no DOM of its own. Uses
 * :func:`usePipecatClientMediaTrack` so it re-applies mute the
 * moment the bot's audio track lands (the hook returns null until
 * the track exists, then re-renders with it).
 */
function BotAudioController({ isMuted }: { isMuted: boolean }) {
    const botAudioTrack = usePipecatClientMediaTrack("audio", "bot");
    useEffect(() => {
        if (botAudioTrack) {
            botAudioTrack.enabled = !isMuted;
        }
    }, [botAudioTrack, isMuted]);
    return null;
}

function MicGlyph({ muted }: { muted: boolean }) {
    return (
        <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
        >
            <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
            <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
            <line x1="12" y1="18" x2="12" y2="22" />
            <line x1="8" y1="22" x2="16" y2="22" />
            {muted && <line x1="3" y1="3" x2="21" y2="21" />}
        </svg>
    );
}

function KeyboardGlyph() {
    return (
        <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
        >
            <rect x="2" y="6" width="20" height="12" rx="2" />
            <line x1="6" y1="10" x2="6" y2="10" />
            <line x1="10" y1="10" x2="10" y2="10" />
            <line x1="14" y1="10" x2="14" y2="10" />
            <line x1="18" y1="10" x2="18" y2="10" />
            <line x1="7" y1="14" x2="17" y2="14" />
        </svg>
    );
}
