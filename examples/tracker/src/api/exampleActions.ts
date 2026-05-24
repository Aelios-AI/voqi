import { useStore } from "../store";
import type { TaskPriority, TaskStatus } from "../store/types";
import { fakeLatency } from "../lib/simulate";
import { parseHumanDate } from "../lib/time";

type ActionResult<T = unknown> = { ok: true; data: T } | { ok: false; error: string };

const ok = <T>(data: T): ActionResult<T> => ({ ok: true, data });
const err = (error: string): ActionResult<never> => ({ ok: false, error });

const resolveAssignee = (input: string | null | undefined): string | null => {
    if (!input) return null;
    const members = useStore.getState().members;
    const direct = members.find((m) => m.id === input);
    if (direct) return direct.id;
    const lower = input.toLowerCase();
    const byName = members.find(
        (m) =>
            m.name.toLowerCase() === lower ||
            m.name.toLowerCase().split(" ")[0] === lower ||
            m.email.toLowerCase().split("@")[0] === lower,
    );
    return byName?.id ?? null;
};

const resolveLabel = (input: string): string | null => {
    const labels = useStore.getState().labels;
    const direct = labels.find((l) => l.id === input);
    if (direct) return direct.id;
    const lower = input.toLowerCase();
    return labels.find((l) => l.name.toLowerCase() === lower)?.id ?? null;
};

const resolveSprint = (input: string | null | undefined): string | null => {
    if (!input) return null;
    const sprints = useStore.getState().sprints;
    const direct = sprints.find((s) => s.id === input);
    if (direct) return direct.id;
    const lower = input.toLowerCase();
    return sprints.find((s) => s.name.toLowerCase().includes(lower))?.id ?? null;
};

const resolveTask = (input: string): string | null => {
    const tasks = useStore.getState().tasks;
    const direct = tasks.find((t) => t.id === input);
    if (direct) return direct.id;
    const lower = input.toLowerCase();
    const byCode = tasks.find((t) => t.code.toLowerCase() === lower);
    if (byCode) return byCode.id;
    return tasks.find((t) => t.title.toLowerCase().includes(lower))?.id ?? null;
};

export const exampleApi = {
    async createTask(input: {
        title: string;
        description?: string;
        status?: TaskStatus;
        priority?: TaskPriority;
        assignee?: string | null;
        labels?: string[];
        sprint?: string | null;
        dueDate?: string | null;
        estimateHours?: number | null;
    }) {
        await fakeLatency();
        const store = useStore.getState();
        if (!input.title?.trim()) return err("Task title is required");
        const labelIds: string[] = [];
        for (const raw of input.labels ?? []) {
            const id = resolveLabel(raw);
            if (id) labelIds.push(id);
        }
        const task = store.createTask({
            title: input.title.trim(),
            description: input.description ?? "",
            status: input.status ?? "backlog",
            priority: input.priority ?? "medium",
            assigneeId: resolveAssignee(input.assignee),
            labelIds,
            sprintId: resolveSprint(input.sprint),
            dueDate: input.dueDate ? parseHumanDate(input.dueDate) : null,
            estimateHours: input.estimateHours ?? null,
        });
        store.pushToast(`Created ${task.code} — ${task.title}`, "success");
        return ok({ id: task.id, code: task.code });
    },

    async updateTask(id: string, patch: Partial<{
        title: string;
        description: string;
        status: TaskStatus;
        priority: TaskPriority;
        estimateHours: number | null;
    }>) {
        await fakeLatency();
        const taskId = resolveTask(id);
        if (!taskId) return err(`Task not found: ${id}`);
        const result = useStore.getState().updateTask(taskId, patch);
        if (!result) return err("Update failed");
        useStore.getState().pushToast(`Updated ${result.code}`, "success");
        return ok({ id: result.id, code: result.code });
    },

    async moveTask(id: string, status: TaskStatus) {
        await fakeLatency();
        const taskId = resolveTask(id);
        if (!taskId) return err(`Task not found: ${id}`);
        const result = useStore.getState().moveTask(taskId, status);
        if (!result) return err("Move failed");
        useStore.getState().pushToast(`Moved ${result.code} → ${status.replace("_", " ")}`, "success");
        return ok({ id: result.id, code: result.code, status: result.status });
    },

    async assignTask(id: string, assignee: string | null) {
        await fakeLatency();
        const taskId = resolveTask(id);
        if (!taskId) return err(`Task not found: ${id}`);
        const result = useStore.getState().assignTask(taskId, resolveAssignee(assignee));
        if (!result) return err("Assign failed");
        useStore.getState().pushToast(`Assigned ${result.code}`, "success");
        return ok({ id: result.id, code: result.code, assigneeId: result.assigneeId });
    },

    async setPriority(id: string, priority: TaskPriority) {
        await fakeLatency();
        const taskId = resolveTask(id);
        if (!taskId) return err(`Task not found: ${id}`);
        const result = useStore.getState().setPriority(taskId, priority);
        if (!result) return err("Priority update failed");
        useStore.getState().pushToast(`${result.code} → ${priority}`, "success");
        return ok({ id: result.id, code: result.code });
    },

    async setDueDate(id: string, dueDate: string | null) {
        await fakeLatency();
        const taskId = resolveTask(id);
        if (!taskId) return err(`Task not found: ${id}`);
        const ts = dueDate ? parseHumanDate(dueDate) : null;
        if (dueDate && ts == null) return err(`Couldn't parse date: ${dueDate}`);
        const result = useStore.getState().setDueDate(taskId, ts);
        if (!result) return err("Due-date update failed");
        useStore.getState().pushToast(`${result.code} due ${dueDate ?? "—"}`, "success");
        return ok({ id: result.id, code: result.code, dueDate: result.dueDate });
    },

    async addLabel(id: string, label: string) {
        await fakeLatency();
        const taskId = resolveTask(id);
        if (!taskId) return err(`Task not found: ${id}`);
        const labelId = resolveLabel(label);
        if (!labelId) return err(`Label not found: ${label}`);
        const result = useStore.getState().addLabel(taskId, labelId);
        if (!result) return err("Label add failed");
        useStore.getState().pushToast(`+${label} on ${result.code}`, "success");
        return ok({ id: result.id, code: result.code });
    },

    async deleteTask(id: string) {
        await fakeLatency();
        const taskId = resolveTask(id);
        if (!taskId) return err(`Task not found: ${id}`);
        const before = useStore.getState().tasks.find((t) => t.id === taskId);
        const removed = useStore.getState().deleteTask(taskId);
        if (!removed) return err("Delete failed");
        useStore.getState().pushToast(`Deleted ${before?.code ?? id}`, "info");
        return ok({ id: taskId });
    },

    async batchMove(ids: string[], status: TaskStatus) {
        await fakeLatency(200, 500);
        const resolved = ids.map(resolveTask).filter((x): x is string => Boolean(x));
        if (!resolved.length) return err("No matching tasks");
        const count = useStore.getState().batchUpdate(resolved, { status });
        useStore.getState().pushToast(`Moved ${count} tasks → ${status.replace("_", " ")}`, "success");
        return ok({ count });
    },

    async batchAssign(ids: string[], assignee: string | null) {
        await fakeLatency(200, 500);
        const resolved = ids.map(resolveTask).filter((x): x is string => Boolean(x));
        if (!resolved.length) return err("No matching tasks");
        const count = useStore.getState().batchUpdate(resolved, {
            assigneeId: resolveAssignee(assignee),
        });
        useStore.getState().pushToast(`Reassigned ${count} tasks`, "success");
        return ok({ count });
    },

    async batchSetPriority(ids: string[], priority: TaskPriority) {
        await fakeLatency(200, 500);
        const resolved = ids.map(resolveTask).filter((x): x is string => Boolean(x));
        if (!resolved.length) return err("No matching tasks");
        const count = useStore.getState().batchUpdate(resolved, { priority });
        useStore.getState().pushToast(`Set ${count} tasks → ${priority}`, "success");
        return ok({ count });
    },

    async batchAddLabel(ids: string[], label: string) {
        await fakeLatency(200, 500);
        const resolved = ids.map(resolveTask).filter((x): x is string => Boolean(x));
        if (!resolved.length) return err("No matching tasks");
        const labelId = resolveLabel(label);
        if (!labelId) return err(`Label not found: ${label}`);
        const count = useStore.getState().batchAddLabel(resolved, labelId);
        useStore.getState().pushToast(`Tagged ${count} tasks with ${label}`, "success");
        return ok({ count });
    },

    async batchAddToSprint(ids: string[], sprint: string | null) {
        await fakeLatency(200, 500);
        const resolved = ids.map(resolveTask).filter((x): x is string => Boolean(x));
        if (!resolved.length) return err("No matching tasks");
        const sprintId = resolveSprint(sprint);
        if (sprint && !sprintId) return err(`Sprint not found: ${sprint}`);
        const count = useStore.getState().batchAddToSprint(resolved, sprintId);
        useStore.getState().pushToast(`Moved ${count} tasks to sprint`, "success");
        return ok({ count });
    },

    async batchDelete(ids: string[]) {
        await fakeLatency(200, 500);
        const resolved = ids.map(resolveTask).filter((x): x is string => Boolean(x));
        if (!resolved.length) return err("No matching tasks");
        const count = useStore.getState().batchDelete(resolved);
        useStore.getState().pushToast(`Deleted ${count} tasks`, "info");
        return ok({ count });
    },

    async addComment(taskId: string, body: string) {
        await fakeLatency();
        const id = resolveTask(taskId);
        if (!id) return err(`Task not found: ${taskId}`);
        if (!body?.trim()) return err("Empty comment");
        const c = useStore.getState().addComment(id, body.trim());
        useStore.getState().pushToast("Comment posted", "success");
        return ok({ commentId: c.id });
    },

    async createSprint(input: { name: string; goal?: string; startDate?: string; endDate?: string }) {
        await fakeLatency();
        if (!input.name?.trim()) return err("Sprint name is required");
        const sprint = useStore.getState().createSprint({
            name: input.name.trim(),
            goal: input.goal,
            startDate: input.startDate ? parseHumanDate(input.startDate) ?? undefined : undefined,
            endDate: input.endDate ? parseHumanDate(input.endDate) ?? undefined : undefined,
        });
        useStore.getState().pushToast(`Created sprint “${sprint.name}”`, "success");
        return ok({ id: sprint.id });
    },

    async startSprint(sprintRef: string) {
        await fakeLatency();
        const id = resolveSprint(sprintRef);
        if (!id) return err(`Sprint not found: ${sprintRef}`);
        const result = useStore.getState().setSprintStatus(id, "active");
        useStore.getState().pushToast(`Started ${result?.name}`, "success");
        return ok({ id });
    },

    async completeSprint(sprintRef: string) {
        await fakeLatency();
        const id = resolveSprint(sprintRef);
        if (!id) return err(`Sprint not found: ${sprintRef}`);
        const result = useStore.getState().setSprintStatus(id, "completed");
        useStore.getState().pushToast(`Completed ${result?.name}`, "success");
        return ok({ id });
    },

    async createMember(input: {
        name: string;
        role?: string;
        email?: string;
        capacityHours?: number;
    }) {
        await fakeLatency();
        if (!input.name?.trim()) return err("Member name is required");
        const m = useStore.getState().createMember(input);
        useStore.getState().pushToast(`Added ${m.name}`, "success");
        return ok({ id: m.id });
    },

    async createLabel(name: string, color?: string) {
        await fakeLatency();
        if (!name?.trim()) return err("Label name is required");
        const l = useStore.getState().createLabel(name.trim(), color);
        useStore.getState().pushToast(`Created label “${l.name}”`, "success");
        return ok({ id: l.id });
    },

    listTasks(filter?: {
        status?: TaskStatus;
        priority?: TaskPriority;
        assignee?: string;
        sprint?: string;
        label?: string;
    }) {
        const tasks = useStore.getState().tasks;
        const labelId = filter?.label ? resolveLabel(filter.label) : null;
        const assigneeId = filter?.assignee ? resolveAssignee(filter.assignee) : null;
        const sprintId = filter?.sprint ? resolveSprint(filter.sprint) : null;
        return tasks.filter((t) => {
            if (filter?.status && t.status !== filter.status) return false;
            if (filter?.priority && t.priority !== filter.priority) return false;
            if (assigneeId && t.assigneeId !== assigneeId) return false;
            if (sprintId && t.sprintId !== sprintId) return false;
            if (labelId && !t.labelIds.includes(labelId)) return false;
            return true;
        });
    },

    listMembers() {
        return useStore.getState().members;
    },

    listLabels() {
        return useStore.getState().labels;
    },

    listSprints() {
        return useStore.getState().sprints;
    },

    searchTasks(query: string) {
        const q = query.toLowerCase();
        return useStore
            .getState()
            .tasks.filter(
                (t) =>
                    t.title.toLowerCase().includes(q) ||
                    t.code.toLowerCase().includes(q) ||
                    t.description.toLowerCase().includes(q),
            );
    },

    selectTasks(ids: string[]) {
        const resolved = ids.map(resolveTask).filter((x): x is string => Boolean(x));
        useStore.getState().selectAll(resolved);
        return ok({ count: resolved.length });
    },

    clearSelection() {
        useStore.getState().clearSelection();
        return ok({});
    },

    navigate(view: "dashboard" | "board" | "list" | "sprints" | "team" | "inbox") {
        const path = view === "dashboard" ? "/" : `/${view}`;
        window.history.pushState({}, "", path);
        window.dispatchEvent(new PopStateEvent("popstate"));
        return ok({ path });
    },
};

declare global {
    interface Window {
        example: typeof exampleApi;
    }
}

export const installExampleApi = (): void => {
    window.example = exampleApi;
};
