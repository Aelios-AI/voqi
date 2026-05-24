import type {
    VoqiMockControl,
    InAppSessionStart,
} from "./types";
import type {
    IInAppTransport,
    TransportEvents,
    TranscriptEntry,
} from "./transport";
import { ToolRegistry } from "./toolRegistry";
import { captureHostScreenshot } from "./screenshot";

interface AutoBehavior {
    autoGreet: boolean;
    autoReady: boolean;
}

/**
 * Fully in-memory transport for UI testing. Implements the same
 * ``IInAppTransport`` surface as the real Pipecat/Daily one, but
 * never opens a WebRTC connection or talks to any backend. Drives the
 * widget through scripted lifecycle events on a timer, and exposes a
 * manual control surface at ``window.__voqiMock`` so a developer
 * can fire transcripts, tool calls, and disconnects from the console.
 */
export class MockInAppTransport implements IInAppTransport {
    private connected = false;
    private muted = false;
    private toolCallCount = 0;
    private autoTimers: Array<ReturnType<typeof setTimeout>> = [];
    private autoBehavior: AutoBehavior = { autoGreet: true, autoReady: true };

    constructor(
        private readonly registry: ToolRegistry,
        private readonly events: TransportEvents,
    ) {
        this.installControlSurface();
    }

    isConnected(): boolean {
        return this.connected;
    }

    async connect(_session: InAppSessionStart): Promise<void> {
        if (this.connected) return;
        this.connected = true;
        // Simulate the WebRTC handshake.
        this.scheduleAuto(400, () => {
            this.events.onConnected?.();
        });
        // Fire agent_ready ~1.5s after Start so the visitor sees the
        // "Connecting…" state for a real beat.
        if (this.autoBehavior.autoReady) {
            this.scheduleAuto(1500, () => {
                this.events.onBotReady?.();
            });
        }
        if (this.autoBehavior.autoGreet) {
            // Greeting drips in at human-readable speed. Mirror the
            // real backend: fire onAssistantTurnStarted right BEFORE
            // onSpeaking so the widget creates an empty placeholder
            // caption for this turn — the streaming-cursor signal
            // attaches to the new bubble immediately, instead of
            // sitting on whatever previous bubble exists.
            this.scheduleAuto(1700, () => {
                this.events.onAssistantTurnStarted?.();
                this.events.onSpeaking?.();
            });
            this.scheduleAuto(2000, () => {
                this.pushBot("Hi! I'm a mocked in-app assistant.");
            });
            this.scheduleAuto(5000, () => {
                this.pushBot(
                    "You can talk, type, mute, or trigger tool calls — all without any backend running.",
                );
            });
            this.scheduleAuto(8000, () => this.events.onIdle?.());
        }
    }

    async disconnect(): Promise<void> {
        this.clearAutoTimers();
        if (!this.connected) return;
        this.connected = false;
        this.events.onDisconnected?.();
    }

    setMicrophoneMuted(muted: boolean): void {
        this.muted = muted;
    }

    sendTextMessage(text: string): void {
        const trimmed = text.trim();
        if (!trimmed || !this.connected) return;
        this.events.onProcessing?.();
        // Fire turn-started + speaking together (matches the real
        // backend's `assistant_turn_started` server message which
        // triggers both). The turn-started call lets the widget push
        // its empty placeholder caption so the streaming cursor
        // appears in a fresh bubble for this turn — not at the end of
        // the previous turn's last sentence.
        this.scheduleAuto(400, () => {
            this.events.onAssistantTurnStarted?.();
            this.events.onSpeaking?.();
        });
        const toolMatch = this.matchToolFromText(trimmed);
        if (toolMatch) {
            this.scheduleAuto(1500, () => {
                this.pushBot(`Sure — running ${toolMatch.name} for you.`);
            });
            this.scheduleAuto(2200, () => {
                void this.simulateToolCall(toolMatch.name, toolMatch.args);
            });
            this.scheduleAuto(4500, () => this.events.onIdle?.());
        } else {
            // Stream a multi-sentence canned reply paced for human
            // reading — a sentence every ~3.5s, total ~15-20s for a
            // 4-sentence reply. Keeps each bubble visible long enough
            // to actually read before the next arrives.
            const replies = this.makeMultiSentenceReply(trimmed);
            const firstDelay = 1200;
            const sentenceGap = 3500;
            replies.forEach((sentence, i) => {
                this.scheduleAuto(firstDelay + i * sentenceGap, () =>
                    this.pushBot(sentence),
                );
            });
            const totalMs = firstDelay + replies.length * sentenceGap + 1000;
            this.scheduleAuto(totalMs, () => this.events.onIdle?.());
        }
    }

    private makeMultiSentenceReply(userText: string): string[] {
        const lower = userText.toLowerCase();
        const head = `Got it — you said "${userText}".`;
        const corpus = [
            "I'm a mocked in-app assistant, so I'm just narrating the experience for you here.",
            "If you mention a tool by name (try 'list tasks' or 'create a task called X'), I'll run a fake round-trip through the registry.",
            "While you're here, notice the caption strip above the pill while I'm talking — it fades when I stop.",
            "The transcript icon to the right of the pill opens the full conversation history if you want to scroll back.",
            "You can mute the mic with the icon in the middle, and click the keyboard to switch into typing mode like you just did.",
            "If your connection drops mid-session, the pill turns red and the toast above tells you to reconnect.",
        ];
        // Weight a handful of relevant sentences first if the user
        // touched on a known concept.
        const front: string[] = [];
        if (lower.includes("transcript") || lower.includes("history"))
            front.push(corpus[3]);
        if (lower.includes("mute") || lower.includes("type"))
            front.push(corpus[4]);
        if (lower.includes("disconnect") || lower.includes("reconnect"))
            front.push(corpus[5]);
        // Always include the head + 3 unique sentences.
        const seen = new Set<string>(front);
        const tail: string[] = [];
        for (const s of corpus) {
            if (tail.length >= 3) break;
            if (seen.has(s)) continue;
            tail.push(s);
            seen.add(s);
        }
        return [head, ...front, ...tail];
    }

    getToolCallCount(): number {
        return this.toolCallCount;
    }

    getPipecatClient(): null {
        // No real PipecatClient in mock mode — there's no inbound
        // audio track to play, so the Widget skips rendering
        // ``<PipecatClientAudio />`` when this returns null.
        return null;
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private pushBot(text: string): void {
        this.events.onTranscript?.({
            role: "assistant",
            content: text,
            timestamp: new Date().toISOString(),
        });
    }

    private matchToolFromText(text: string): {
        name: string;
        args: Record<string, unknown>;
    } | null {
        const lower = text.toLowerCase();
        if (lower.includes("list") && lower.includes("task")) {
            return { name: "list_tasks", args: {} };
        }
        if (lower.includes("create") && lower.includes("task")) {
            const nameMatch = text.match(/called\s+([A-Za-z0-9 ]{1,40})/i);
            return {
                name: "create_task",
                args: { name: nameMatch ? nameMatch[1].trim() : "Untitled" },
            };
        }
        if (lower.includes("delete") && lower.includes("task")) {
            return { name: "delete_task", args: { id: "t-1" } };
        }
        return null;
    }

    private async simulateToolCall(
        name: string,
        args: Record<string, unknown>,
    ): Promise<unknown> {
        this.toolCallCount++;
        const callId = `mock-call-${this.toolCallCount}-${Date.now()}`;
        const batchId = `mock-batch-${this.toolCallCount}`;
        try {
            const outcome = await this.registry.dispatch(batchId, {
                call_id: callId,
                name,
                args,
            });
            this.scheduleAuto(300, () => {
                this.pushBot(
                    outcome.error
                        ? `(${name} returned an error: ${outcome.error})`
                        : `(${name} returned successfully)`,
                );
                this.events.onIdle?.();
            });
            return outcome;
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.scheduleAuto(300, () => {
                this.pushBot(`(${name} threw: ${message})`);
                this.events.onIdle?.();
            });
            return { error: message };
        }
    }

    private scheduleAuto(delayMs: number, fn: () => void): void {
        const t = setTimeout(() => {
            fn();
            this.autoTimers = this.autoTimers.filter((x) => x !== t);
        }, delayMs);
        this.autoTimers.push(t);
    }

    private clearAutoTimers(): void {
        for (const t of this.autoTimers) clearTimeout(t);
        this.autoTimers = [];
    }

    /**
     * Hang the manual control surface off ``window.__voqiMock`` so
     * developers can drive the widget from the browser console. Each
     * call routes back into the regular event callbacks so the widget
     * processes mock events through exactly the same code paths as
     * real ones.
     */
    private installControlSurface(): void {
        if (typeof window === "undefined") return;
        const ctl: VoqiMockControl = {
            simulateUserSpeech: (text: string) => {
                if (!this.connected) return;
                const entry: TranscriptEntry = {
                    role: "user",
                    content: text,
                    timestamp: new Date().toISOString(),
                };
                this.events.onListening?.();
                this.events.onTranscript?.(entry);
                this.events.onProcessing?.();
            },
            simulateBotMessage: (text: string) => {
                if (!this.connected) return;
                // Open a turn for this manual message so the widget's
                // placeholder + streaming-cursor wiring behaves the
                // same way as a real backend turn.
                this.events.onAssistantTurnStarted?.();
                this.events.onSpeaking?.();
                this.pushBot(text);
            },
            fireAgentReady: () => {
                this.events.onBotReady?.();
            },
            setAgentState: (state) => {
                if (!this.connected) return;
                if (state === "listening") this.events.onListening?.();
                else if (state === "processing") this.events.onProcessing?.();
                else if (state === "responding") this.events.onSpeaking?.();
                else this.events.onIdle?.();
            },
            simulateToolCall: (name, args = {}) =>
                this.simulateToolCall(name, args),
            disconnect: () => {
                void this.disconnect();
            },
            simulateConnectionLoss: () => {
                if (!this.connected) return;
                this.clearAutoTimers();
                this.connected = false;
                this.events.onUnexpectedDisconnect?.();
                this.events.onDisconnected?.();
            },
            simulateError: (message: string) => {
                this.events.onError?.(message);
            },
            setAutoBehavior: ({ autoGreet, autoReady }) => {
                if (typeof autoGreet === "boolean")
                    this.autoBehavior.autoGreet = autoGreet;
                if (typeof autoReady === "boolean")
                    this.autoBehavior.autoReady = autoReady;
            },
            triggerScreenshotCapture: () => captureHostScreenshot(),
            simulateIdleTimeoutEnd: () => {
                if (!this.connected) return;
                this.events.onSessionEnding?.({
                    reason: "idle_grace_elapsed",
                    lingerSeconds: 0,
                });
            },
            simulateGuideCursor: (
                x: number = 0.5,
                y: number = 0.5,
                label: string = "Click here",
            ) => {
                if (!this.connected) return;
                // Same shape the real ``guide_cursor`` server-message
                // path produces. Routing through ``onGuideCursor``
                // means the widget renders + auto-fades the cursor
                // exactly like the production path; nothing is
                // mock-special.
                this.events.onGuideCursor?.({ x, y, label });
            },
            simulatePermissionDenied: (message) => {
                // Mirror the real transport's behaviour on a
                // mid-session DeviceError: stop the auto-greet/auto-
                // ready timers (no more bot output should arrive) and
                // fire the dedicated event so the widget freezes in
                // the trouble state. Crucially, leave `connected =
                // true` so `transport.isConnected()` reports the
                // session is still alive — the widget keeps `session`
                // set and the End button stays visible until the
                // visitor clicks it, exactly like the unexpected-
                // disconnect path. Tear-down happens when End fires.
                this.clearAutoTimers();
                this.events.onPermissionDenied?.(
                    message ?? "Microphone permission denied",
                );
            },
        };
        // Touch the muted flag so TS doesn't flag it as unused — also
        // useful for tests that want to assert the mute state.
        (ctl as VoqiMockControl & { isMuted: () => boolean }).isMuted = () =>
            this.muted;
        window.__voqiMock = ctl;
    }
}
