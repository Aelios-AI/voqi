/**
 * Example Tracker ↔ Voqi adapter.
 *
 * Registers tool handlers against `window.VoqiReady` so the
 * agent can drive the Example Tracker store. Handlers accept human
 * identifiers (task code, member name, sprint name, label name) and
 * resolve them to store IDs internally — the agent never has to know
 * our UUIDs.
 *
 * Each tool ships:
 *   - `parameters`: JSON schema (mandatory under the new contract —
 *      the widget ships these to the bot at session start).
 *   - `requiresConfirmation`: flagged on destructive tools so the
 *      visitor's permission is required.
 */
import { useStore } from "./store";
import type {
    Task,
    Member,
    Sprint,
    Label,
    SprintStatus,
    TaskPriority,
    TaskStatus,
} from "./store/types";

declare global {
    interface Window {
        VoqiReady?: Array<(api: VoqiApi) => void>;
        // Bridged by <NavBridge> in App.tsx so this module (which lives
        // outside React) can drive react-router programmatically.
        __trackerNavigate?: (path: string) => void;
    }
}

interface VoqiToolDef {
    name: string;
    description: string;
    parameters: Record<string, unknown>;
    execute: (args: Record<string, unknown>) => unknown | Promise<unknown>;
    requiresConfirmation?: boolean;
}

interface VoqiApi {
    defineTool: (def: VoqiToolDef) => void;
}

// ────────────────────────────────────────────────────────── resolvers

const resolveTask = (idOrCode: string): Task | null => {
    const tasks = useStore.getState().tasks;
    const target = idOrCode.trim().toLowerCase();
    return (
        tasks.find((t) => t.id.toLowerCase() === target) ??
        tasks.find((t) => t.code.toLowerCase() === target) ??
        null
    );
};

const resolveMember = (nameOrId: string): Member | null => {
    const members = useStore.getState().members;
    const target = nameOrId.trim().toLowerCase();
    return (
        members.find((m) => m.id.toLowerCase() === target) ??
        members.find((m) => m.name.toLowerCase() === target) ??
        members.find((m) => m.name.toLowerCase().includes(target)) ??
        null
    );
};

const resolveSprint = (nameOrId: string): Sprint | null => {
    const sprints = useStore.getState().sprints;
    const target = nameOrId.trim().toLowerCase();
    return (
        sprints.find((s) => s.id.toLowerCase() === target) ??
        sprints.find((s) => s.name.toLowerCase() === target) ??
        sprints.find((s) => s.name.toLowerCase().includes(target)) ??
        null
    );
};

const resolveLabel = (nameOrId: string): Label | null => {
    const labels = useStore.getState().labels;
    const target = nameOrId.trim().toLowerCase();
    return (
        labels.find((l) => l.id.toLowerCase() === target) ??
        labels.find((l) => l.name.toLowerCase() === target) ??
        labels.find((l) => l.name.toLowerCase().includes(target)) ??
        null
    );
};

const parseDateMs = (raw: unknown): number | null => {
    if (raw == null || raw === "") return null;
    if (typeof raw === "number") return raw;
    const ms = Date.parse(String(raw));
    return Number.isFinite(ms) ? ms : null;
};

const summarizeTask = (t: Task) => ({
    id: t.id,
    code: t.code,
    title: t.title,
    status: t.status,
    priority: t.priority,
    assigneeId: t.assigneeId,
    sprintId: t.sprintId,
    labelIds: t.labelIds,
    dueDate: t.dueDate,
});

// ────────────────────────────────────────────────────────── shared schema fragments

const TASK_REF_PROP = {
    type: "string",
    description: "Task code (e.g. EX-12) or id. Codes are easier — the agent normally has them in conversation.",
};
const MEMBER_REF_PROP = {
    type: "string",
    description: "Member name (full name or substring) or id.",
};
const SPRINT_REF_PROP = {
    type: "string",
    description: "Sprint name (or substring) or id.",
};
const LABEL_REF_PROP = {
    type: "string",
    description: "Label name (case-insensitive) or id.",
};
const STATUS_ENUM = ["backlog", "in_progress", "review", "done"] as const;
const PRIORITY_ENUM = ["urgent", "high", "medium", "low"] as const;
const SPRINT_STATUS_ENUM = ["planned", "active", "completed"] as const;

// ────────────────────────────────────────────────────────── registration

window.VoqiReady = window.VoqiReady || [];
window.VoqiReady.push((Voqi) => {
    Voqi.configure({
        agentUrl: "http://localhost:3002/start",
        branding: { position: "bottom-right" },
    });

    const s = () => useStore.getState();

    // ── Navigation ───────────────────────────────────────────────────
    // Map friendly page keys (what the visitor says aloud, what the
    // sidebar labels show) to the actual react-router routes.
    const PAGE_ROUTES: Record<string, string> = {
        dashboard: "/",
        board: "/board",
        list: "/list",
        inbox: "/inbox",
        sprints: "/sprints",
        team: "/team",
        settings: "/settings",
    };

    Voqi.defineTool({
        name: "navigate_to_page",
        description:
            "Move the visitor to a different page of the tracker. " +
            "Use when the visitor asks to 'go to', 'open', 'show me', " +
            "or 'take me to' a page by name — Dashboard (the overview), " +
            "Board (kanban), List (task list), Inbox, Sprints, Team " +
            "(members), or Settings. Returns the new path on success.",
        parameters: {
            type: "object",
            properties: {
                page: {
                    type: "string",
                    enum: Object.keys(PAGE_ROUTES),
                    description:
                        "Page key. dashboard = overview, board = kanban, " +
                        "list = task list, inbox = inbox, sprints = sprint " +
                        "planning, team = members, settings = workspace settings.",
                },
            },
            required: ["page"],
        },
        execute: async (args) => {
            const page = String(args.page ?? "").toLowerCase();
            const route = PAGE_ROUTES[page];
            if (!route) {
                return {
                    error: `Unknown page '${page}'. Choose from: ${Object.keys(
                        PAGE_ROUTES,
                    ).join(", ")}.`,
                };
            }
            const nav = window.__trackerNavigate;
            if (!nav) {
                return {
                    error:
                        "Navigation bridge not ready — the host React tree " +
                        "may still be mounting. Try again in a moment.",
                };
            }
            nav(route);
            return { ok: true, page, route };
        },
    });

    // ── Tasks: read-only ─────────────────────────────────────────────
    Voqi.defineTool({
        name: "list_tasks",
        description:
            "List tasks with optional filters. Use when the visitor asks 'what's open?', 'show me Alice's bugs', etc. " +
            "Pure read — does not change the UI.",
        parameters: {
            type: "object",
            properties: {
                status: { type: "string", enum: [...STATUS_ENUM], description: "Filter by status." },
                priority: { type: "string", enum: [...PRIORITY_ENUM], description: "Filter by priority." },
                assignee: { ...MEMBER_REF_PROP, description: "Filter by assignee (member name or id)." },
                sprint: { ...SPRINT_REF_PROP, description: "Filter by sprint (name or id)." },
                label: { ...LABEL_REF_PROP, description: "Filter by label (name or id)." },
            },
        },
        execute: async (args) => {
            let tasks = s().tasks.slice();
            if (args.status) tasks = tasks.filter((t) => t.status === args.status);
            if (args.priority) tasks = tasks.filter((t) => t.priority === args.priority);
            if (args.assignee) {
                const m = resolveMember(String(args.assignee));
                tasks = tasks.filter((t) => t.assigneeId === (m?.id ?? null));
            }
            if (args.sprint) {
                const sp = resolveSprint(String(args.sprint));
                tasks = tasks.filter((t) => t.sprintId === (sp?.id ?? null));
            }
            if (args.label) {
                const l = resolveLabel(String(args.label));
                tasks = l ? tasks.filter((t) => t.labelIds.includes(l.id)) : [];
            }
            return tasks.slice(0, 50).map(summarizeTask);
        },
    });

    Voqi.defineTool({
        name: "get_task",
        description:
            "Get full details for a single task by code (e.g. EX-12) or id. Pure read.",
        parameters: {
            type: "object",
            properties: { task: TASK_REF_PROP },
            required: ["task"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            return t ? summarizeTask(t) : { error: "Task not found" };
        },
    });

    // ── Tasks: mutations ─────────────────────────────────────────────
    Voqi.defineTool({
        name: "create_task",
        description:
            "Create a new task. Title is required; everything else is optional. The new task appears in the list/board on save.",
        parameters: {
            type: "object",
            properties: {
                title: { type: "string", description: "Required. Short title for the task." },
                description: { type: "string", description: "Longer description body." },
                status: { type: "string", enum: [...STATUS_ENUM], description: "Defaults to 'backlog'." },
                priority: { type: "string", enum: [...PRIORITY_ENUM], description: "Defaults to 'medium'." },
                assignee: MEMBER_REF_PROP,
                sprint: SPRINT_REF_PROP,
                dueDate: { type: "string", description: "ISO date string. Optional." },
                labels: {
                    type: "array",
                    items: { type: "string" },
                    description: "Label names to apply.",
                },
            },
            required: ["title"],
        },
        execute: async (args) => {
            const assignee = args.assignee ? resolveMember(String(args.assignee)) : null;
            const sprint = args.sprint ? resolveSprint(String(args.sprint)) : null;
            const labelIds: string[] = Array.isArray(args.labels)
                ? (args.labels as string[])
                      .map((n) => resolveLabel(n)?.id)
                      .filter((x): x is string => Boolean(x))
                : [];
            const created = s().createTask({
                title: String(args.title),
                description: args.description ? String(args.description) : "",
                status: (args.status as TaskStatus) ?? "backlog",
                priority: (args.priority as TaskPriority) ?? "medium",
                assigneeId: assignee?.id ?? null,
                sprintId: sprint?.id ?? null,
                dueDate: parseDateMs(args.dueDate),
                labelIds,
            });
            return summarizeTask(created);
        },
    });

    Voqi.defineTool({
        name: "update_task",
        description:
            "Update fields on an existing task. Only provided fields change. Use when the visitor wants to edit a single task.",
        parameters: {
            type: "object",
            properties: {
                task: TASK_REF_PROP,
                title: { type: "string" },
                description: { type: "string" },
                status: { type: "string", enum: [...STATUS_ENUM] },
                priority: { type: "string", enum: [...PRIORITY_ENUM] },
                dueDate: { type: "string", description: "ISO date string, or empty to clear." },
            },
            required: ["task"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            if (!t) return { error: "Task not found" };
            const patch: Partial<Task> = {};
            if (args.title != null) patch.title = String(args.title);
            if (args.description != null) patch.description = String(args.description);
            if (args.status) patch.status = args.status as TaskStatus;
            if (args.priority) patch.priority = args.priority as TaskPriority;
            if (args.dueDate !== undefined) patch.dueDate = parseDateMs(args.dueDate);
            const updated = s().updateTask(t.id, patch);
            return updated ? summarizeTask(updated) : { error: "Update failed" };
        },
    });

    Voqi.defineTool({
        name: "delete_task",
        description: "Permanently delete a task by code or id. Cannot be undone.",
        parameters: {
            type: "object",
            properties: { task: TASK_REF_PROP },
            required: ["task"],
        },
        requiresConfirmation: true,
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            if (!t) return { error: "Task not found" };
            return { ok: s().deleteTask(t.id), code: t.code };
        },
    });

    Voqi.defineTool({
        name: "move_task",
        description:
            "Move a task to a new status. Use when the visitor says 'mark X as done', 'move to in progress', etc.",
        parameters: {
            type: "object",
            properties: {
                task: TASK_REF_PROP,
                status: { type: "string", enum: [...STATUS_ENUM] },
            },
            required: ["task", "status"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            if (!t) return { error: "Task not found" };
            const updated = s().moveTask(t.id, args.status as TaskStatus);
            return updated ? summarizeTask(updated) : { error: "Move failed" };
        },
    });

    Voqi.defineTool({
        name: "set_task_priority",
        description: "Set a task's priority.",
        parameters: {
            type: "object",
            properties: {
                task: TASK_REF_PROP,
                priority: { type: "string", enum: [...PRIORITY_ENUM] },
            },
            required: ["task", "priority"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            if (!t) return { error: "Task not found" };
            const updated = s().setPriority(t.id, args.priority as TaskPriority);
            return updated ? summarizeTask(updated) : { error: "Update failed" };
        },
    });

    Voqi.defineTool({
        name: "assign_task",
        description:
            "Assign a task to a member by name. Pass empty string or omit to unassign.",
        parameters: {
            type: "object",
            properties: {
                task: TASK_REF_PROP,
                assignee: { ...MEMBER_REF_PROP, description: "Member name or id. Omit / empty to unassign." },
            },
            required: ["task"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            if (!t) return { error: "Task not found" };
            const assigneeId = args.assignee ? resolveMember(String(args.assignee))?.id ?? null : null;
            const updated = s().assignTask(t.id, assigneeId);
            return updated ? summarizeTask(updated) : { error: "Assign failed" };
        },
    });

    Voqi.defineTool({
        name: "set_task_due_date",
        description: "Set or clear a task's due date. Accepts ISO string; omit / empty to clear.",
        parameters: {
            type: "object",
            properties: {
                task: TASK_REF_PROP,
                dueDate: { type: "string", description: "ISO date string. Empty string to clear." },
            },
            required: ["task"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            if (!t) return { error: "Task not found" };
            const updated = s().setDueDate(t.id, parseDateMs(args.dueDate));
            return updated ? summarizeTask(updated) : { error: "Update failed" };
        },
    });

    Voqi.defineTool({
        name: "add_label_to_task",
        description: "Add a label to a task (label by name).",
        parameters: {
            type: "object",
            properties: {
                task: TASK_REF_PROP,
                label: LABEL_REF_PROP,
            },
            required: ["task", "label"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            const l = resolveLabel(String(args.label));
            if (!t) return { error: "Task not found" };
            if (!l) return { error: "Label not found" };
            const updated = s().addLabel(t.id, l.id);
            return updated ? summarizeTask(updated) : { error: "Update failed" };
        },
    });

    Voqi.defineTool({
        name: "remove_label_from_task",
        description: "Remove a label from a task.",
        parameters: {
            type: "object",
            properties: {
                task: TASK_REF_PROP,
                label: LABEL_REF_PROP,
            },
            required: ["task", "label"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            const l = resolveLabel(String(args.label));
            if (!t) return { error: "Task not found" };
            if (!l) return { error: "Label not found" };
            const updated = s().removeLabel(t.id, l.id);
            return updated ? summarizeTask(updated) : { error: "Update failed" };
        },
    });

    Voqi.defineTool({
        name: "add_task_to_sprint",
        description:
            "Move a task into a sprint by sprint name. Pass empty string to remove from sprint.",
        parameters: {
            type: "object",
            properties: {
                task: TASK_REF_PROP,
                sprint: { ...SPRINT_REF_PROP, description: "Sprint name. Empty to remove from sprint." },
            },
            required: ["task"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            if (!t) return { error: "Task not found" };
            const sprintId = args.sprint ? resolveSprint(String(args.sprint))?.id ?? null : null;
            const updated = s().addToSprint(t.id, sprintId);
            return updated ? summarizeTask(updated) : { error: "Update failed" };
        },
    });

    // ── Batch ────────────────────────────────────────────────────────
    // No getTarget on batch tools — they touch many rows; pointing at
    // a single one would be misleading.
    Voqi.defineTool({
        name: "batch_update_tasks",
        description: "Update status, priority, assignee, sprint, or due date on many tasks at once.",
        parameters: {
            type: "object",
            properties: {
                tasks: {
                    type: "array",
                    items: { type: "string" },
                    description: "Array of task codes (e.g. ['EX-12', 'EX-13']) or ids.",
                },
                status: { type: "string", enum: [...STATUS_ENUM] },
                priority: { type: "string", enum: [...PRIORITY_ENUM] },
                assignee: { ...MEMBER_REF_PROP, description: "Empty string to unassign." },
                sprint: { ...SPRINT_REF_PROP, description: "Empty string to remove from sprint." },
                dueDate: { type: "string", description: "ISO date string. Empty to clear." },
            },
            required: ["tasks"],
        },
        execute: async (args) => {
            const ids = (args.tasks as string[]).map((x) => resolveTask(x)?.id).filter((x): x is string => Boolean(x));
            const patch: Record<string, unknown> = {};
            if (args.status) patch.status = args.status;
            if (args.priority) patch.priority = args.priority;
            if (args.assignee !== undefined) {
                patch.assigneeId = args.assignee ? resolveMember(String(args.assignee))?.id ?? null : null;
            }
            if (args.sprint !== undefined) {
                patch.sprintId = args.sprint ? resolveSprint(String(args.sprint))?.id ?? null : null;
            }
            if (args.dueDate !== undefined) patch.dueDate = parseDateMs(args.dueDate);
            return { updated: s().batchUpdate(ids, patch) };
        },
    });

    Voqi.defineTool({
        name: "batch_delete_tasks",
        description: "Delete many tasks in one call. Cannot be undone.",
        parameters: {
            type: "object",
            properties: {
                tasks: {
                    type: "array",
                    items: { type: "string" },
                    description: "Array of task codes or ids.",
                },
            },
            required: ["tasks"],
        },
        requiresConfirmation: true,
        execute: async (args) => {
            const ids = (args.tasks as string[]).map((x) => resolveTask(x)?.id).filter((x): x is string => Boolean(x));
            return { deleted: s().batchDelete(ids) };
        },
    });

    Voqi.defineTool({
        name: "batch_add_label",
        description: "Apply a label to many tasks at once.",
        parameters: {
            type: "object",
            properties: {
                tasks: { type: "array", items: { type: "string" } },
                label: LABEL_REF_PROP,
            },
            required: ["tasks", "label"],
        },
        execute: async (args) => {
            const ids = (args.tasks as string[]).map((x) => resolveTask(x)?.id).filter((x): x is string => Boolean(x));
            const l = resolveLabel(String(args.label));
            if (!l) return { error: "Label not found" };
            return { updated: s().batchAddLabel(ids, l.id) };
        },
    });

    // ── Comments ─────────────────────────────────────────────────────
    Voqi.defineTool({
        name: "add_comment",
        description: "Post a comment on a task.",
        parameters: {
            type: "object",
            properties: {
                task: TASK_REF_PROP,
                body: { type: "string", description: "Comment text." },
            },
            required: ["task", "body"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            if (!t) return { error: "Task not found" };
            return s().addComment(t.id, String(args.body));
        },
    });

    Voqi.defineTool({
        name: "delete_comment",
        description: "Delete a comment by id. Cannot be undone.",
        parameters: {
            type: "object",
            properties: {
                comment: { type: "string", description: "Comment id." },
            },
            required: ["comment"],
        },
        requiresConfirmation: true,
        execute: async (args) => ({ ok: s().deleteComment(String(args.comment)) }),
    });

    // ── Sprints ──────────────────────────────────────────────────────
    Voqi.defineTool({
        name: "list_sprints",
        description: "List all sprints with their status and date ranges. Pure read.",
        parameters: { type: "object", properties: {} },
        execute: async () => s().sprints,
    });

    Voqi.defineTool({
        name: "create_sprint",
        description: "Create a new sprint.",
        parameters: {
            type: "object",
            properties: {
                name: { type: "string" },
                goal: { type: "string", description: "Sprint goal / theme." },
                startDate: { type: "string", description: "ISO date string." },
                endDate: { type: "string", description: "ISO date string." },
            },
            required: ["name"],
        },
        execute: async (args) =>
            s().createSprint({
                name: String(args.name),
                goal: args.goal ? String(args.goal) : undefined,
                startDate: parseDateMs(args.startDate) ?? undefined,
                endDate: parseDateMs(args.endDate) ?? undefined,
            }),
    });

    Voqi.defineTool({
        name: "update_sprint",
        description: "Update a sprint's name, goal, or dates.",
        parameters: {
            type: "object",
            properties: {
                sprint: SPRINT_REF_PROP,
                name: { type: "string" },
                goal: { type: "string" },
                startDate: { type: "string" },
                endDate: { type: "string" },
            },
            required: ["sprint"],
        },
        execute: async (args) => {
            const sp = resolveSprint(String(args.sprint));
            if (!sp) return { error: "Sprint not found" };
            const patch: Partial<Sprint> = {};
            if (args.name != null) patch.name = String(args.name);
            if (args.goal != null) patch.goal = String(args.goal);
            if (args.startDate !== undefined) {
                const v = parseDateMs(args.startDate);
                if (v != null) patch.startDate = v;
            }
            if (args.endDate !== undefined) {
                const v = parseDateMs(args.endDate);
                if (v != null) patch.endDate = v;
            }
            return s().updateSprint(sp.id, patch) ?? { error: "Update failed" };
        },
    });

    Voqi.defineTool({
        name: "set_sprint_status",
        description: "Change a sprint's status.",
        parameters: {
            type: "object",
            properties: {
                sprint: SPRINT_REF_PROP,
                status: { type: "string", enum: [...SPRINT_STATUS_ENUM] },
            },
            required: ["sprint", "status"],
        },
        execute: async (args) => {
            const sp = resolveSprint(String(args.sprint));
            if (!sp) return { error: "Sprint not found" };
            return s().setSprintStatus(sp.id, args.status as SprintStatus) ?? { error: "Update failed" };
        },
    });

    Voqi.defineTool({
        name: "delete_sprint",
        description: "Delete a sprint. Cannot be undone.",
        parameters: {
            type: "object",
            properties: { sprint: SPRINT_REF_PROP },
            required: ["sprint"],
        },
        requiresConfirmation: true,
        execute: async (args) => {
            const sp = resolveSprint(String(args.sprint));
            if (!sp) return { error: "Sprint not found" };
            return { ok: s().deleteSprint(sp.id) };
        },
    });

    // ── Members ──────────────────────────────────────────────────────
    Voqi.defineTool({
        name: "list_members",
        description: "List all team members with their role and capacity. Pure read.",
        parameters: { type: "object", properties: {} },
        execute: async () => s().members,
    });

    Voqi.defineTool({
        name: "create_member",
        description: "Add a new team member.",
        parameters: {
            type: "object",
            properties: {
                name: { type: "string" },
                role: { type: "string", description: "Job role / title." },
                email: { type: "string" },
                capacityHours: { type: "number", description: "Sprint capacity in hours." },
            },
            required: ["name"],
        },
        execute: async (args) =>
            s().createMember({
                name: String(args.name),
                role: args.role ? String(args.role) : undefined,
                email: args.email ? String(args.email) : undefined,
                capacityHours: args.capacityHours ? Number(args.capacityHours) : undefined,
            }),
    });

    Voqi.defineTool({
        name: "update_member",
        description: "Update a team member's name, role, email, or capacity.",
        parameters: {
            type: "object",
            properties: {
                member: MEMBER_REF_PROP,
                name: { type: "string" },
                role: { type: "string" },
                email: { type: "string" },
                capacityHours: { type: "number" },
            },
            required: ["member"],
        },
        execute: async (args) => {
            const m = resolveMember(String(args.member));
            if (!m) return { error: "Member not found" };
            const patch: Partial<Member> = {};
            if (args.name != null) patch.name = String(args.name);
            if (args.role != null) patch.role = String(args.role);
            if (args.email != null) patch.email = String(args.email);
            if (args.capacityHours != null) patch.capacityHours = Number(args.capacityHours);
            return s().updateMember(m.id, patch) ?? { error: "Update failed" };
        },
    });

    Voqi.defineTool({
        name: "archive_member",
        description: "Archive (remove from active roster) a team member by name. Cannot be undone from this UI.",
        parameters: {
            type: "object",
            properties: { member: MEMBER_REF_PROP },
            required: ["member"],
        },
        requiresConfirmation: true,
        execute: async (args) => {
            const m = resolveMember(String(args.member));
            if (!m) return { error: "Member not found" };
            return { ok: s().archiveMember(m.id) };
        },
    });

    // ── Labels ───────────────────────────────────────────────────────
    Voqi.defineTool({
        name: "list_labels",
        description: "List all available labels. Pure read.",
        parameters: { type: "object", properties: {} },
        execute: async () => s().labels,
    });

    Voqi.defineTool({
        name: "create_label",
        description: "Create a new label.",
        parameters: {
            type: "object",
            properties: {
                name: { type: "string" },
                color: { type: "string", description: "Hex (e.g. #3B82F6) or named color." },
            },
            required: ["name"],
        },
        execute: async (args) =>
            s().createLabel(String(args.name), args.color ? String(args.color) : undefined),
    });

    Voqi.defineTool({
        name: "delete_label",
        description: "Delete a label by name or id. Cannot be undone.",
        parameters: {
            type: "object",
            properties: { label: LABEL_REF_PROP },
            required: ["label"],
        },
        requiresConfirmation: true,
        execute: async (args) => {
            const l = resolveLabel(String(args.label));
            if (!l) return { error: "Label not found" };
            return { ok: s().deleteLabel(l.id) };
        },
    });

    // ── UI helpers ───────────────────────────────────────────────────
    Voqi.defineTool({
        name: "open_task_drawer",
        description:
            "Open the side drawer showing a task's full detail. Useful when the visitor says 'show me EX-12' or 'open it up'.",
        parameters: {
            type: "object",
            properties: { task: TASK_REF_PROP },
            required: ["task"],
        },
        execute: async (args) => {
            const t = resolveTask(String(args.task));
            if (!t) return { error: "Task not found" };
            s().openDrawer(t.id);
            return { ok: true, code: t.code };
        },
    });
});
