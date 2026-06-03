/**
 * Dev-only entry point for the widget UI test page.
 *
 * Mounts the widget in mock mode by importing the source directly,
 * so there is NO build step needed — Vite serves the TS source over
 * its module pipeline. Visit ``/widget-mock-test.html`` on the dev
 * server to use it.
 *
 * The production embed flow (the IIFE bundle compiled by
 * ``vite.widget.config.ts``) is unchanged; this file is purely for
 * local dev/test convenience.
 */
import "./index";

// Pre-register sample tools and mount in mock mode. The widget's
// auto-mount path looks for a script tag with data-public-key — in
// source-import dev mode there is none, so it bails (logging a
// harmless console error). We mount explicitly here with mock:true.
window.AeliosSparkReady = window.AeliosSparkReady || [];
window.AeliosSparkReady.push((api) => {
    api.defineTool({
        name: "list_tasks",
        description: "List tasks",
        parameters: { type: "object", properties: {} },
        execute: async () => [
            { id: "t-1", title: "Ship release notes" },
            { id: "t-2", title: "Review PR #482" },
        ],
    });
    api.defineTool({
        name: "create_task",
        description: "Create a task",
        parameters: {
            type: "object",
            properties: {
                title: { type: "string", description: "Task title" },
            },
            required: ["title"],
        },
        execute: async (args) => ({ ok: true, id: "t-new", ...args }),
    });
    api.defineTool({
        name: "delete_task",
        description: "Delete a task",
        parameters: {
            type: "object",
            properties: {
                id: { type: "string", description: "Task id" },
            },
            required: ["id"],
        },
        execute: async (args) => ({ ok: true, deleted: args.id }),
    });
    api.mount({ mock: true });
});
