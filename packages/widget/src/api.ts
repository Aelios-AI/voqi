import type {
    InAppMode,
    InAppSessionStart,
    InAppWidgetConfig,
    InAppWidgetTool,
    VoqiUserConfig,
} from "./types";

/**
 * Local-only API shim — replaces the HTTP control plane the managed
 * Voqi product uses with values the host page supplies via
 * ``Voqi.configure({...})``. Nothing here makes a network call.
 *
 * The widget code is written against this small interface, so the
 * mock client (mockApi.ts) and the local client (this file) are both
 * drop-in replacements.
 */
export interface IInAppApi {
    getConfig(): Promise<InAppWidgetConfig>;
    startSession(params: {
        tools: InAppWidgetTool[];
        language: string;
        mode: InAppMode;
    }): Promise<InAppSessionStart>;
}

function mintUuid(): string {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
    }
    // Acceptable fallback for older browsers — collisions are vanishingly
    // unlikely at our cardinality (one session per visitor).
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export class LocalInAppApi implements IInAppApi {
    constructor(private readonly config: VoqiUserConfig) {}

    async getConfig(): Promise<InAppWidgetConfig> {
        const branding = this.config.branding ?? {};
        return {
            branding: {
                position: branding.position ?? "bottom-right",
            },
            themeColors: branding.themeColors ?? null,
        };
    }

    async startSession(params: {
        tools: InAppWidgetTool[];
        language: string;
        mode: InAppMode;
    }): Promise<InAppSessionStart> {
        return {
            sessionUuid: mintUuid(),
            inAppAgentUuid: "voqi-local",
            token: { token: "", expiresIn: 60 * 60 * 2 },
            // OSS-only: the agent server reads these directly out of the
            // /start body — there is no control plane to fetch them from.
            tools: params.tools,
            language: params.language,
            mode: params.mode,
            agentUrl: this.config.agentUrl,
        };
    }
}
