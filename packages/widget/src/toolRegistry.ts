import type {
    VoqiToolDefinition,
    InAppWidgetTool,
    ToolResultClientMessage,
} from "./types";

/** One tool inside a `tool_call_batch` server message. */
export interface BatchedToolCall {
    call_id: string;
    name: string;
    args: Record<string, unknown>;
}

/**
 * In-memory map of tool name → host-supplied callback. Populated by the host
 * page via `Voqi.defineTool(...)`. The runtime looks up here whenever the
 * agent emits a `tool_call` message.
 *
 * This mirrors the host-trusting philosophy of OpenAI's
 * realtime-voice-component: the widget never sees host application state
 * directly. Tools are the only interface, and the host owns all action
 * logic.
 */
export class ToolRegistry {
    private readonly tools = new Map<string, VoqiToolDefinition>();
    private readonly invocationLog: Array<{
        toolName: string;
        args: unknown;
        result: unknown;
        timestamp: string;
    }> = [];

    define(def: VoqiToolDefinition): void {
        if (!def?.name || typeof def.execute !== "function") {
            console.error("[Voqi] defineTool requires { name, execute }", def);
            return;
        }
        if (this.tools.has(def.name)) {
            console.warn(
                `[Voqi] defineTool overwriting existing registration for "${def.name}"`,
            );
        }
        this.tools.set(def.name, def);
    }

    has(name: string): boolean {
        return this.tools.has(name);
    }

    list(): VoqiToolDefinition[] {
        return Array.from(this.tools.values());
    }

    /**
     * Strip host-only fields (`execute`) and serialize the registered
     * tools into the wire shape the bot needs at session start. The
     * bot only sees name + description + parameter schema +
     * confirmation flag — never the actual functions.
     */
    listSchemas(): InAppWidgetTool[] {
        return Array.from(this.tools.values()).map((t) => ({
            name: t.name,
            description: t.description,
            parametersJsonSchema: t.parameters,
            requiresConfirmation: t.requiresConfirmation === true,
        }));
    }

    log(): ReadonlyArray<{
        toolName: string;
        args: unknown;
        result: unknown;
        timestamp: string;
    }> {
        return this.invocationLog;
    }

    async dispatch(
        batchId: string,
        call: BatchedToolCall,
    ): Promise<ToolResultClientMessage> {
        const tool = this.tools.get(call.name);
        if (!tool) {
            return {
                type: "tool_result",
                batch_id: batchId,
                call_id: call.call_id,
                error: `Tool "${call.name}" is not registered on this site`,
            };
        }

        try {
            const result = await tool.execute(call.args ?? {});
            const safeResult = sanitizeForTransport(result);
            this.invocationLog.push({
                toolName: call.name,
                args: call.args ?? {},
                result: safeResult,
                timestamp: new Date().toISOString(),
            });
            return {
                type: "tool_result",
                batch_id: batchId,
                call_id: call.call_id,
                result: safeResult,
            };
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            this.invocationLog.push({
                toolName: call.name,
                args: call.args ?? {},
                result: { error: message },
                timestamp: new Date().toISOString(),
            });
            return {
                type: "tool_result",
                batch_id: batchId,
                call_id: call.call_id,
                error: message,
            };
        }
    }
}

/**
 * Tools commonly return DOM nodes, class instances, or other non-cloneable
 * values. Strip those so the data channel send never throws — the agent
 * only needs JSON-serializable feedback.
 */
function sanitizeForTransport(value: unknown): unknown {
    try {
        return JSON.parse(JSON.stringify(value ?? null));
    } catch {
        return { ok: true };
    }
}
