import type { IInAppApi } from "./api";
import type {
    InAppMode,
    InAppSessionStart,
    InAppWidgetConfig,
    InAppWidgetTool,
    InAppWidgetThemeColors,
} from "./types";

declare global {
    interface Window {
        /** Mock-only hook: the dev test page can assign a theme-colour
         *  object here before mount and the mock config will surface it
         *  as `themeColors`, so the widget themes itself end-to-end
         *  without a backend. See widget-mock-test.html for the
         *  catalog and a theme switcher. */
        __aeliosSparkMockThemeColors?: InAppWidgetThemeColors;
    }
}

/**
 * In-memory ``IInAppApi`` for UI test mode. Returns canned config
 * + a synthetic session token. Never touches the network. Pair with
 * :class:`MockInAppTransport` to run the widget completely offline.
 */
export class MockInAppApi implements IInAppApi {
    async getConfig(): Promise<InAppWidgetConfig> {
        return {
            branding: {
                launcherShape: "pill",
                position: "bottom-right",
            },
            themeColors:
                typeof window !== "undefined" && window.__aeliosSparkMockThemeColors
                    ? window.__aeliosSparkMockThemeColors
                    : null,
        };
    }

    async startSession(_params: {
        tools: InAppWidgetTool[];
        language: string;
        mode: InAppMode;
    }): Promise<InAppSessionStart> {
        return {
            sessionUuid: `mock-session-${Date.now()}`,
            inAppAgentUuid: "mock-agent-uuid",
            token: { token: "mock-token-not-real", expiresIn: 7200 },
        };
    }

}
