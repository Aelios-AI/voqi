import { PipecatClient, RTVIEvent } from "@pipecat-ai/client-js";
import { DailyTransport } from "@pipecat-ai/daily-transport";
import type {
    InAppSessionStart,
    ToolCallBatchServerMessage,
    ToolResultClientMessage,
} from "./types";
import { ToolRegistry } from "./toolRegistry";
import { captureHostScreenshot } from "./screenshot";

export interface TranscriptEntry {
    role: "user" | "assistant";
    content: string;
    timestamp: string;
    /** User entries only. ``"voice"`` for STT transcripts coming from
     * the bot's ``user_message`` server message — these merge into a
     * preceding user bubble so consecutive STT chunks render as one
     * utterance. ``"text"`` for messages typed via the chat input —
     * each typed message is its own bubble and acts as a barrier
     * (it never merges into a previous bubble and a later user
     * message never merges into it). Undefined for assistant
     * entries; assistants always render as separate bubbles. */
    source?: "voice" | "text";
}

/**
 * Minimal transport surface the Widget consumes. The production
 * :class:`InAppTransport` (Daily / Pipecat) and the
 * :class:`MockInAppTransport` (in-memory, no network) both
 * implement this. Adding a new method here means thinking about how
 * the mock simulates it.
 */
export interface IInAppTransport {
    isConnected(): boolean;
    connect(session: InAppSessionStart): Promise<void>;
    disconnect(): Promise<void>;
    setMicrophoneMuted(muted: boolean): void;
    sendTextMessage(text: string): void;
    getToolCallCount(): number;
    /** The underlying ``PipecatClient`` instance, exposed so the
     * widget can wrap ``<PipecatClientAudio />`` inside a
     * ``<PipecatClientProvider client={client}>``. The Pipecat React
     * audio component is what creates the ``<audio>`` element that
     * actually plays the bot's inbound audio track — without it
     * Daily receives the track but there's nothing on the page to
     * route it to the speakers.
     *
     * Returns ``null`` while not connected (initial state, post-
     * disconnect) AND in mock mode (no real PipecatClient — the
     * MockInAppTransport has no audio to play). */
    getPipecatClient(): PipecatClient | null;
}

export interface TransportEvents {
    /** WebRTC + RTVI data channel established. NOT the same as
     * "agent ready to converse" — for that, listen to onBotReady. */
    onConnected?: () => void;
    /** The bot's pipeline is fully wired up and ready to handle input.
     * Pipecat fires this once the server's RTVIProcessor calls
     * set_bot_ready (which happens automatically when the client sends
     * its client-ready message after WebRTC + data channel are up). */
    onBotReady?: () => void;
    /** Disconnect that the user explicitly requested via disconnect(). */
    onDisconnected?: () => void;
    /** Disconnect that happened WITHOUT the user clicking End — network
     * blip, server crash, bot machine kill, etc. The widget should
     * surface this clearly so the visitor knows to reconnect. */
    onUnexpectedDisconnect?: () => void;
    onListening?: () => void;
    onProcessing?: () => void;
    onSpeaking?: (preview?: string) => void;
    onIdle?: () => void;
    onTranscript?: (entry: TranscriptEntry) => void;
    /** A new assistant turn just started. Fires before any of that
     * turn's sentence-level ``onTranscript`` events. The widget uses
     * this as a hard break so two sentences from different turns can
     * NEVER merge into one transcript bubble, regardless of how
     * close in time they arrive. */
    onAssistantTurnStarted?: () => void;
    onError?: (message: string) => void;
    /** The BOT decided to end the session and is about to tear down.
     * Widget should render the bot's just-spoken goodbye, linger for
     * the suggested seconds, then disconnect on its own — that way the
     * visitor sees a clean end instead of the WebRTC channel
     * disappearing first (which would otherwise surface the generic
     * "Connection lost" banner). Reason discriminates the flavour
     * (e.g. visitor_confirmed_end vs idle_grace_elapsed). */
    onSessionEnding?: (info: { reason: string; lingerSeconds: number }) => void;
    /** The browser refused to give us mic access — visitor clicked Block,
     * the host site has no mic permission policy, hardware busy, etc.
     * Fires once per attempted Start. The widget should treat this as a
     * hard end of the session attempt: tear everything down and surface
     * a "permission denied" state distinct from a generic connection
     * error so the visitor knows it's their browser/OS that needs
     * fixing, not our backend. */
    onPermissionDenied?: (message: string) => void;
    /** Guide-mode pointing hint. Bot picked a normalized (x, y) on the
     * screenshot it saw and a short on-screen label; the widget
     * multiplies by the live viewport to place the ghost cursor. Only
     * fires in guide mode — action-mode bot never emits this. */
    onGuideCursor?: (hint: { x: number; y: number; label: string }) => void;
}

/**
 * Voice transport: Daily WebRTC + RTVI data channel.
 *
 * Thin coordinator on the widget side: open the room, dispatch tool
 * calls into the host-supplied registry, hand results back over the
 * data channel, surface lifecycle events to the Widget shell.
 */
export class InAppTransport implements IInAppTransport {
    private client: PipecatClient | null = null;
    private toolCallCount = 0;
    /** Set by disconnect(); used to differentiate intended end-of-
     * session vs. unexpected drop in the disconnected event. */
    private endRequested = false;
    /**
     * Heartbeat interval. We send ``{type:"heartbeat"}`` to the server
     * every 3 seconds. The server's heartbeat monitor (see
     * ``InAppAgentProcessor._heartbeat_timeout_monitor``) checks
     * ``_last_heartbeat_time`` every 5 seconds and tears down the bot
     * if 60 seconds elapse with no heartbeat — that's how a frozen tab
     * / sleeping laptop / stale WebRTC channel gets cleaned up
     * server-side. The inverse direction (bot went silent on the
     * widget) is covered by Pipecat's WebRTC layer firing
     * ``disconnected`` and our ``onUnexpectedDisconnect`` callback.
     */
    private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
    /** Cancellation handles for the /start polling loop. The fetch is
     * aborted and the inter-attempt sleep is woken so the loop exits
     * within a tick of disconnect(), with no residual requests. */
    private pollAbortController: AbortController | null = null;
    private pollSleepTimer: ReturnType<typeof setTimeout> | null = null;
    private pollSleepWake: (() => void) | null = null;
    /**
     * Serial-execution queue across batches; tools within a single
     * batch run in parallel (see ``enqueueBatch``). Each batch arrives
     * as one atomic `tool_call_batch` message carrying N tool calls.
     * If a second batch arrives while the first is still draining,
     * it's enqueued to start once every tool in the first batch has
     * resolved — that way batch B can rely on batch A's writes having
     * landed.
     */
    private batchQueue: Promise<void> = Promise.resolve();

    constructor(
        private readonly voiceAgentUrl: string,
        private readonly registry: ToolRegistry,
        private readonly events: TransportEvents,
    ) {}

    isConnected(): boolean {
        return this.client !== null;
    }

    async connect(session: InAppSessionStart): Promise<void> {
        if (this.client) {
            console.warn("[AeliosSpark] transport already connected — skipping");
            return;
        }

        this.endRequested = false;

        const client = new PipecatClient({
            transport: new DailyTransport(),
            // Start muted at the SDK level — same pattern the demo
            // meeting page uses (``enableMic: false``). The visitor
            // chose to start a session, but they haven't explicitly
            // chosen to talk yet; the kickoff greeting will tell
            // them they can unmute or switch to typing. Without this
            // flag the WebRTC layer publishes the mic from the very
            // first moment of the connection.
            enableMic: false,
            enableCam: false,
        });

        // RTVIEvent.BotReady fires when the bot's RTVIProcessor is
        // wired up and the SDK transitions to its internal "ready"
        // state — that's the gate `sendClientRequest` checks. Heartbeat
        // (and any other outbound request) MUST wait for this event,
        // not the earlier "connected" transport event, otherwise every
        // send throws "transport not in ready state".
        //
        // We still expose a separate "agent ready to converse" signal
        // — the bot's custom `agent_ready` server message, fired on the
        // first LLM token — via the serverMessage handler below. That
        // controls the UX flip ("say something now"); BotReady controls
        // SDK readiness.
        client.on(RTVIEvent.BotReady, () => {
            this.events.onConnected?.();
            this.startHeartbeat();
        });
        client.on(RTVIEvent.Disconnected, () => {
            const wasIntentional = this.endRequested;
            this.stopHeartbeat();
            this.client = null;
            this.endRequested = false;
            this.events.onDisconnected?.();
            if (!wasIntentional) {
                this.events.onUnexpectedDisconnect?.();
            }
        });

        // Listen/think/speak FSM is driven entirely by custom server
        // messages, not Pipecat's built-in user/bot speaking events.
        // The bot emits explicit `user_turn_started` / `user_turn_ended`
        // / `assistant_turn_started` / `assistant_turn_ended` server
        // messages from the aggregator turn handlers (mirrors the demo's
        // pattern). See the serverMessage handler below — every state
        // transition flows through there for one consistent, debuggable
        // path with no surprises from VAD edge-cases.

        // Bot transcripts come from OUR custom ``assistant_message``
        // server message (emitted by the processor at the end of every
        // LLM round + every canned-speech turn — see
        // ``_run_one_round`` and ``_handle_canned_speech``). We
        // intentionally do NOT use Pipecat's native
        // ``RTVIEvent.BotTranscript``: that fires per sentence and
        // would force the widget into client-side coalescing. Our
        // path fires ONCE per assistant turn with the canonical full
        // text, mirroring the user_message pattern below.
        // The handler is in the ``ServerMessage`` switch below.

        // User transcripts come from OUR custom ``user_message`` server
        // message (see ``_handle_user_input`` in the processor). We
        // intentionally do NOT use Pipecat's native ``UserTranscript``
        // event:
        //   * Our path fires ONCE per utterance, AFTER the processor
        //     has accepted it (`_handle_user_input` validated it,
        //     extracted from the LLM context, deduped via
        //     `_processing_message`). What lands here is exactly what
        //     the agent will reason about.
        //   * Pipecat's ``UserTranscript`` fires raw from STT —
        //     interim + final, and will fire even on transcripts the
        //     processor ends up dropping (e.g. mid-cancellation
        //     races). Using it means the widget can render bubbles
        //     for utterances the bot never actually processed.
        // The handler for the ``user_message`` server message is in
        // the ``ServerMessage`` switch below.

        // Server messages: tool_call_batch (round-trip via the registry —
        // tools within a batch run in parallel, batches are serialized
        // against each other) and turn-lifecycle markers that drive the
        // launcher state.
        client.on(RTVIEvent.ServerMessage, (data: unknown) => {
            const msg = (data ?? {}) as { type?: string; from?: string };
            // Pipecat hands us the unwrapped server-message payload
            // directly — NOT an RTVIMessage wrapper. Bot stamps
            // `from: "bot"` via AdaptedRTVIProcessor; ignore anything
            // not from the bot (own loopbacks).
            if (msg.from !== undefined && msg.from !== "bot") {
                return;
            }
            if (isToolCallBatch(data)) {
                this.enqueueBatch(data);
                return;
            }
            const type = msg.type;
            switch (type) {
                case "agent_ready":
                    this.events.onBotReady?.();
                    break;
                case "user_message": {
                    // The processor's ``_handle_user_input`` echoes
                    // the visitor's utterance to us via this server
                    // message AFTER it has accepted the transcript
                    // for processing. This is the canonical "what
                    // the agent heard" signal — preferred over
                    // Pipecat's raw RTVIEvent.UserTranscript (which
                    // also fires for interim + dropped transcripts).
                    const payload = (data ?? {}) as { text?: string };
                    if (typeof payload.text === "string" && payload.text) {
                        this.events.onTranscript?.({
                            role: "user",
                            content: payload.text,
                            timestamp: new Date().toISOString(),
                        });
                    }
                    break;
                }
                case "assistant_message": {
                    // Symmetric to user_message: the processor emits
                    // this once per LLM round (so multiple times per
                    // Pipecat assistant_turn when a tool-call chain
                    // runs without user input between rounds — the
                    // aggregator collapses those into one turn). The
                    // widget renders each emit as its own bubble.
                    // Replaces Pipecat's per-sentence
                    // RTVIEvent.BotTranscript.
                    const payload = (data ?? {}) as { text?: string };
                    if (typeof payload.text === "string" && payload.text) {
                        this.events.onTranscript?.({
                            role: "assistant",
                            content: payload.text,
                            timestamp: new Date().toISOString(),
                        });
                    }
                    break;
                }
                case "user_turn_started":
                    this.events.onListening?.();
                    break;
                case "user_turn_ended":
                    this.events.onProcessing?.();
                    break;
                case "assistant_turn_started":
                    this.events.onAssistantTurnStarted?.();
                    this.events.onSpeaking?.();
                    break;
                case "assistant_turn_ended":
                    this.events.onIdle?.();
                    break;
                case "assistant_interrupted":
                    // Do NOT call onIdle here. Interruption usually
                    // fires right after `user_turn_started` (visitor
                    // started talking over the bot); calling onIdle
                    // would clobber the "listening" agentState within
                    // milliseconds, so the visitor never sees the mic
                    // glyph while they're actually speaking. Let the
                    // user-driven events (user_turn_started → listening,
                    // user_turn_ended → processing) carry the state.
                    break;
                case "request_screenshot": {
                    // The agent is about to run an inference and wants
                    // a snapshot of the host page to attach to its
                    // human-marker message. Capture asynchronously and
                    // post the bytes back keyed by the same request_id.
                    // Errors are reported back as a non-success
                    // payload so the server's awaiter can give up
                    // immediately instead of hitting its 2s timeout.
                    const payload = (data ?? {}) as { request_id?: string };
                    const requestId = payload.request_id;
                    if (typeof requestId !== "string" || !requestId) break;
                    void this.handleScreenshotRequest(requestId);
                    break;
                }
                case "guide_cursor": {
                    // Guide-mode pointing hint. Bot saw the visitor's
                    // current screenshot and decided "click here". The
                    // payload is normalized — x and y are fractions of
                    // the screenshot the LLM saw, in [0, 1] — so we
                    // multiply by the live viewport on the widget side
                    // when rendering the ghost cursor. Action mode
                    // never emits this; ignoring it on the action-side
                    // bot is also safe.
                    const payload = (data ?? {}) as {
                        x?: number;
                        y?: number;
                        label?: string;
                    };
                    const x = typeof payload.x === "number" ? payload.x : null;
                    const y = typeof payload.y === "number" ? payload.y : null;
                    const label =
                        typeof payload.label === "string" ? payload.label : "";
                    if (x === null || y === null) break;
                    this.events.onGuideCursor?.({ x, y, label });
                    break;
                }
                case "session_ending": {
                    // Bot decided to close. Read the suggested linger
                    // (default 4s) and the reason; hand off to the
                    // widget which renders the goodbye and disconnects
                    // proactively. We mark endRequested=true so when
                    // PipecatClient.disconnect() finally fires after
                    // the linger, the disconnected event is treated
                    // as an INTENTIONAL end (no "Connection lost"
                    // banner).
                    const payload = (data ?? {}) as {
                        reason?: string;
                        linger_seconds?: number;
                    };
                    this.endRequested = true;
                    this.events.onSessionEnding?.({
                        reason: payload.reason ?? "unknown",
                        lingerSeconds:
                            typeof payload.linger_seconds === "number"
                                ? payload.linger_seconds
                                : 4,
                    });
                    break;
                }
                default:
                    break;
            }
        });

        client.on(RTVIEvent.Error, (err: unknown) => {
            const message = err instanceof Error ? err.message : String(err);
            this.events.onError?.(message);
        });

        // Mic / camera permission denial. Pipecat fires this when
        // getUserMedia rejects — visitor clicked Block on the browser
        // prompt, host site has no mic permissions policy, hardware
        // contention, etc. Distinct from the generic `error` channel:
        // we surface a dedicated state so the visitor sees "Microphone
        // permission denied" rather than the misleading "Connection
        // lost", and we tear the session down the same way End does.
        client.on(RTVIEvent.DeviceError, (err: unknown) => {
            const e = err as { type?: string; message?: string };
            const message = e?.message ?? "Device permission denied";
            const isMic =
                e?.type === "mic" ||
                /permission|denied|notallowed/i.test(message);
            if (isMic) {
                this.events.onPermissionDenied?.(message);
            } else {
                this.events.onError?.(message);
            }
        });

        // The voice agent's /start endpoint may need to spin up a worker
        // (cold start) and respond with a transient error meaning "not
        // ready yet, try again shortly". We poll for up to 5 minutes,
        // with a 10s per-request timeout and a 2s pause between attempts.
        // Any error short of the total cap is treated as retryable —
        // for a public-facing widget the user's network is as often the
        // culprit as the backend. After 5 minutes we surface a hard
        // connection error and stop. Note: this polling is ONLY for the
        // voice-agent /start; the upstream /in-app/widget/session
        // call is one-shot — if that fails it's a config/auth error,
        // retrying won't fix it.
        const POLL_TIMEOUT_MS = 5 * 60 * 1000;
        const REQUEST_TIMEOUT_MS = 10_000;
        const POLL_INTERVAL_MS = 2_000;
        const startTime = Date.now();

        let payload: unknown = null;
        try {
            while (true) {
                if (this.endRequested) {
                    throw new Error("connect cancelled");
                }
                if (Date.now() - startTime > POLL_TIMEOUT_MS) {
                    const err = new Error(
                        "Voice agent did not become available in time",
                    );
                    this.events.onError?.(err.message);
                    throw err;
                }

                const controller = new AbortController();
                this.pollAbortController = controller;
                const timeoutId = window.setTimeout(
                    () => controller.abort(),
                    REQUEST_TIMEOUT_MS,
                );

                try {
                    const response = await fetch(this.voiceAgentUrl, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            createDailyRoom: true,
                            body: {
                                session_uuid: session.sessionUuid,
                                tools: session.tools ?? [],
                                language: session.language ?? "en",
                                mode: session.mode ?? "action",
                            },
                        }),
                        signal: controller.signal,
                    });
                    window.clearTimeout(timeoutId);
                    this.pollAbortController = null;

                    if (this.endRequested) throw new Error("connect cancelled");
                    if (!response.ok) {
                        throw new Error(`voice-agent error ${response.status}`);
                    }
                    payload = await response.json();
                    break;
                } catch (err) {
                    window.clearTimeout(timeoutId);
                    this.pollAbortController = null;
                    if (this.endRequested) throw err;
                    console.log(
                        "[AeliosSpark] voice agent unavailable, retrying...",
                        err,
                    );
                    // Sleep that wakes early on disconnect so we don't
                    // sit on a 2s timer after the user clicks End.
                    await new Promise<void>((resolve) => {
                        this.pollSleepWake = resolve;
                        this.pollSleepTimer = setTimeout(resolve, POLL_INTERVAL_MS);
                    });
                    this.pollSleepTimer = null;
                    this.pollSleepWake = null;
                }
            }
        } finally {
            this.pollAbortController = null;
            if (this.pollSleepTimer) {
                clearTimeout(this.pollSleepTimer);
                this.pollSleepTimer = null;
            }
            this.pollSleepWake = null;
        }

        // Publish `this.client` BEFORE the final cancellation check so a
        // racing disconnect() can find this client and dispose it. If we
        // checked endRequested first and disconnect ran in the gap
        // before client.connect(), the WebRTC handshake would still kick
        // off against a transport nobody references — a zombie. Now
        // disconnect captures the client, nulls our ref, and awaits its
        // teardown; the recheck below sees endRequested === true and
        // bails out without ever calling client.connect().
        this.client = client;
        if (this.endRequested) {
            this.client = null;
            try {
                await client.disconnect();
            } catch {
                // best-effort
            }
            throw new Error("connect cancelled");
        }

        // Fire-and-forget; lifecycle drives the rest via RTVI events.
        client.connect(payload);
    }

    setMicrophoneMuted(muted: boolean): void {
        try {
            this.client?.enableMic(!muted);
        } catch (err) {
            console.warn("[AeliosSpark] failed to toggle mic", err);
        }
    }

    /**
     * Send a typed message to the bot. The Python side handles this on
     * the `send-text-message` RTVI client message — same path the
     * Layer 3 `text_message_during_in_flight_voice_batch` scenario
     * exercises. Treats the typed text as a fresh user turn (interrupts
     * any in-flight batch).
     */
    sendTextMessage(text: string): void {
        const trimmed = text.trim();
        if (!trimmed || !this.client) return;
        try {
            (this.client as unknown as {
                sendClientRequest?: (type: string, data: unknown) => unknown;
            }).sendClientRequest?.("send-text-message", { text: trimmed, from: "client" });
            // Intentionally NOT mirroring the user's text into the
            // local transcript: the caption stack is assistant-only,
            // so we have nowhere to show it. The text travels straight
            // to the backend and the agent's reply is what surfaces.
        } catch (err) {
            console.warn("[AeliosSpark] failed to send text message", err);
        }
    }

    getToolCallCount(): number {
        return this.toolCallCount;
    }

    getPipecatClient(): PipecatClient | null {
        return this.client;
    }

    async disconnect(): Promise<void> {
        const client = this.client;
        this.endRequested = true;
        this.stopHeartbeat();
        this.client = null;

        // Cancel any in-flight /start polling so a user who clicks End
        // mid-cold-start doesn't leave a residual fetch + sleep timer
        // running in the background.
        if (this.pollAbortController) {
            this.pollAbortController.abort();
            this.pollAbortController = null;
        }
        if (this.pollSleepTimer) {
            clearTimeout(this.pollSleepTimer);
            this.pollSleepTimer = null;
        }
        if (this.pollSleepWake) {
            this.pollSleepWake();
            this.pollSleepWake = null;
        }

        if (!client) return;
        try {
            await client.disconnect();
        } catch (err) {
            console.warn("[AeliosSpark] disconnect threw", err);
        }
    }

    /**
     * Start sending ``{type:"heartbeat"}`` to the server every 3
     * seconds. Safe to call multiple times — clears any prior interval
     * first so we never end up with overlapping heartbeats.
     */
    private startHeartbeat(): void {
        console.log("[AeliosSpark] startHeartbeat() interval starting");
        this.stopHeartbeat();
        let beat = 0;
        this.heartbeatTimer = setInterval(() => {
            beat++;
            const client = this.client as unknown as {
                sendClientRequest?: (type: string, data: unknown) => unknown;
            } | null;
            if (!client?.sendClientRequest) {
                console.warn(`[AeliosSpark] heartbeat #${beat}: no client/sendClientRequest`);
                return;
            }
            try {
                client.sendClientRequest("heartbeat", { from: "client" });
                console.log(`[AeliosSpark] → heartbeat #${beat} sent`);
            } catch (err) {
                console.warn(`[AeliosSpark] → heartbeat #${beat} threw`, err);
            }
        }, 3000);
    }

    private stopHeartbeat(): void {
        if (this.heartbeatTimer !== null) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
    }

    /**
     * Capture a screenshot of the host page and post it back as a
     * ``screenshot_response`` client message keyed by ``requestId``.
     * Never throws — capture failures (CSP, html2canvas internal
     * error, timeout) are reported back as a non-success payload so
     * the server's awaiter resolves to ``None`` immediately rather
     * than waiting out its 2s budget.
     */
    private async handleScreenshotRequest(requestId: string): Promise<void> {
        const result = await captureHostScreenshot();
        const client = this.client as unknown as {
            sendClientRequest?: (type: string, data: unknown) => unknown;
        } | null;
        if (!client?.sendClientRequest) return;
        try {
            if (result.ok) {
                client.sendClientRequest("screenshot_response", {
                    request_id: requestId,
                    image_b64: result.image_b64,
                    mime: result.mime,
                    width: result.width,
                    height: result.height,
                    from: "client",
                });
            } else {
                client.sendClientRequest("screenshot_response", {
                    request_id: requestId,
                    error: result.error,
                    from: "client",
                });
            }
        } catch (err) {
            console.warn("[AeliosSpark] failed to send screenshot response", err);
        }
    }

    private sendToolResult(result: ToolResultClientMessage): void {
        if (!this.client) return;
        try {
            // Pipecat's RTVI client exposes a generic client→server message
            // channel. The Python side treats `tool_result` messages as the
            // counterpart to a tool inside a previously-emitted
            // `tool_call_batch`. Each result carries the batch_id +
            // call_id so the server can drop late results from a batch
            // that was already finalized.
            (this.client as unknown as {
                sendClientRequest?: (type: string, data: unknown) => unknown;
            }).sendClientRequest?.(result.type, { ...result, from: "client" });
        } catch (err) {
            console.warn("[AeliosSpark] failed to send tool result", err);
        }
    }

    /**
     * Within a batch, all tools run in parallel — each result fires
     * back to the server as soon as it lands, and the server matches
     * results by ``call_id`` (the Python side keeps a per-call_id
     * future map, no positional assumption). Across batches we still
     * wait for the previous batch to fully drain before starting the
     * next, so a later batch can rely on the earlier batch's writes
     * having landed.
     */
    private enqueueBatch(batch: ToolCallBatchServerMessage): void {
        const tasks = batch.tool_calls.slice();
        this.batchQueue = this.batchQueue.then(async () => {
            await Promise.all(
                tasks.map(async (call) => {
                    this.toolCallCount++;
                    try {
                        const result = await this.registry.dispatch(
                            batch.batch_id,
                            call,
                        );
                        this.sendToolResult(result);
                    } catch (err) {
                        const message =
                            err instanceof Error ? err.message : String(err);
                        this.sendToolResult({
                            type: "tool_result",
                            batch_id: batch.batch_id,
                            call_id: call.call_id,
                            error: message,
                        });
                    }
                }),
            );
        });
    }
}

function isToolCallBatch(
    value: unknown,
): value is ToolCallBatchServerMessage {
    if (!value || typeof value !== "object") return false;
    const v = value as Record<string, unknown>;
    return (
        v.type === "tool_call_batch" &&
        typeof v.batch_id === "string" &&
        Array.isArray(v.tool_calls)
    );
}
