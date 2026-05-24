import { create } from "zustand";
import { newId, newTaskCode } from "../lib/ids";
import { SEED_LABELS, SEED_MEMBERS, SEED_SPRINTS, SEED_TASKS } from "./seed";
import type {
    ActivityItem,
    Comment,
    Label,
    Member,
    Sprint,
    SprintStatus,
    Task,
    TaskPriority,
    TaskStatus,
} from "./types";

const CURRENT_USER_ID = "m_amelia";

export interface ToastItem {
    id: string;
    message: string;
    kind?: "success" | "error" | "info";
}

interface State {
    tasks: Task[];
    sprints: Sprint[];
    members: Member[];
    labels: Label[];
    comments: Comment[];
    activity: ActivityItem[];
    toasts: ToastItem[];
    drawerTaskId: string | null;
    selectedTaskIds: string[];
    currentUserId: string;
}

interface Actions {
    // tasks
    createTask: (input: Partial<Omit<Task, "id" | "code" | "createdAt" | "updatedAt">> & { title: string }) => Task;
    updateTask: (id: string, patch: Partial<Omit<Task, "id" | "code" | "createdAt">>) => Task | null;
    deleteTask: (id: string) => boolean;
    moveTask: (id: string, status: TaskStatus) => Task | null;
    setPriority: (id: string, priority: TaskPriority) => Task | null;
    assignTask: (id: string, assigneeId: string | null) => Task | null;
    setDueDate: (id: string, dueDate: number | null) => Task | null;
    addLabel: (id: string, labelId: string) => Task | null;
    removeLabel: (id: string, labelId: string) => Task | null;
    addToSprint: (taskId: string, sprintId: string | null) => Task | null;

    // batch
    batchUpdate: (
        ids: string[],
        patch: Partial<Pick<Task, "status" | "priority" | "assigneeId" | "sprintId" | "dueDate">>,
    ) => number;
    batchDelete: (ids: string[]) => number;
    batchAddLabel: (ids: string[], labelId: string) => number;
    batchAddToSprint: (ids: string[], sprintId: string | null) => number;

    // comments
    addComment: (taskId: string, body: string, authorId?: string) => Comment;
    deleteComment: (id: string) => boolean;

    // sprints
    createSprint: (input: { name: string; goal?: string; startDate?: number; endDate?: number }) => Sprint;
    updateSprint: (id: string, patch: Partial<Omit<Sprint, "id">>) => Sprint | null;
    setSprintStatus: (id: string, status: SprintStatus) => Sprint | null;
    deleteSprint: (id: string) => boolean;

    // members
    createMember: (input: { name: string; role?: string; email?: string; capacityHours?: number }) => Member;
    updateMember: (id: string, patch: Partial<Omit<Member, "id">>) => Member | null;
    archiveMember: (id: string) => boolean;

    // labels
    createLabel: (name: string, color?: string) => Label;
    deleteLabel: (id: string) => boolean;

    // ui
    openDrawer: (id: string | null) => void;
    selectTask: (id: string, multi?: boolean) => void;
    clearSelection: () => void;
    selectAll: (ids: string[]) => void;
    pushToast: (message: string, kind?: ToastItem["kind"]) => void;
    dismissToast: (id: string) => void;

    // utility
    reset: () => void;
}

type Store = State & Actions;

const palette = ["#a3b8e0", "#d8c3a3", "#c5a3d8", "#a3d8c5", "#d8a3a3", "#a3c5d8"];

const recordActivity = (state: State, item: Omit<ActivityItem, "id" | "createdAt">): ActivityItem => {
    const entry: ActivityItem = { ...item, id: newId("act"), createdAt: Date.now() };
    state.activity = [entry, ...state.activity].slice(0, 200);
    return entry;
};

const findMemberName = (members: Member[], id: string | null): string => {
    if (!id) return "Unassigned";
    return members.find((m) => m.id === id)?.name ?? "Unknown";
};

// No persist middleware on purpose. Example Tracker is a sample
// workspace — state lives in memory while the tab is open, and a
// hard reload resets to the seed data so each run starts clean.
export const useStore = create<Store>()(
        (set, get) => ({
            tasks: SEED_TASKS,
            sprints: SEED_SPRINTS,
            members: SEED_MEMBERS,
            labels: SEED_LABELS,
            comments: [
                {
                    id: newId("c"),
                    taskId: SEED_TASKS[3].id,
                    authorId: "m_jonas",
                    body: "Confirmed — repro is the SWR cache key not invalidating after a manual move. PR up shortly.",
                    createdAt: Date.now() - 6 * 60 * 60 * 1000,
                },
                {
                    id: newId("c"),
                    taskId: SEED_TASKS[9].id,
                    authorId: "m_yuki",
                    body: "Looked at the trace — it's the N+1 on sprint membership. Adding a join.",
                    createdAt: Date.now() - 90 * 60 * 1000,
                },
            ],
            activity: [],
            toasts: [],
            drawerTaskId: null,
            selectedTaskIds: [],
            currentUserId: CURRENT_USER_ID,

            createTask: (input) => {
                const now = Date.now();
                const task: Task = {
                    id: newId("t"),
                    code: newTaskCode(),
                    title: input.title,
                    description: input.description ?? "",
                    status: input.status ?? "backlog",
                    priority: input.priority ?? "medium",
                    assigneeId: input.assigneeId ?? null,
                    labelIds: input.labelIds ?? [],
                    sprintId: input.sprintId ?? null,
                    dueDate: input.dueDate ?? null,
                    estimateHours: input.estimateHours ?? null,
                    createdAt: now,
                    updatedAt: now,
                };
                set((s) => {
                    const next = { ...s, tasks: [task, ...s.tasks] };
                    recordActivity(next, {
                        kind: "task_created",
                        summary: `created ${task.code} — “${task.title}”`,
                        actorId: s.currentUserId,
                        targetId: task.id,
                    });
                    return next;
                });
                return task;
            },

            updateTask: (id, patch) => {
                let updated: Task | null = null;
                set((s) => ({
                    tasks: s.tasks.map((t) => {
                        if (t.id !== id) return t;
                        updated = { ...t, ...patch, updatedAt: Date.now() };
                        return updated!;
                    }),
                }));
                return updated;
            },

            deleteTask: (id) => {
                let removed = false;
                set((s) => {
                    const exists = s.tasks.some((t) => t.id === id);
                    if (!exists) return s;
                    removed = true;
                    return {
                        tasks: s.tasks.filter((t) => t.id !== id),
                        comments: s.comments.filter((c) => c.taskId !== id),
                        drawerTaskId: s.drawerTaskId === id ? null : s.drawerTaskId,
                        selectedTaskIds: s.selectedTaskIds.filter((x) => x !== id),
                    };
                });
                return removed;
            },

            moveTask: (id, status) => {
                let updated: Task | null = null;
                set((s) => {
                    const next = {
                        ...s,
                        tasks: s.tasks.map((t) => {
                            if (t.id !== id) return t;
                            updated = { ...t, status, updatedAt: Date.now() };
                            return updated!;
                        }),
                    };
                    if (updated) {
                        recordActivity(next, {
                            kind: status === "done" ? "task_completed" : "task_moved",
                            summary: `moved ${updated.code} → ${status.replace("_", " ")}`,
                            actorId: s.currentUserId,
                            targetId: id,
                        });
                    }
                    return next;
                });
                return updated;
            },

            setPriority: (id, priority) => get().updateTask(id, { priority }),
            assignTask: (id, assigneeId) => {
                const result = get().updateTask(id, { assigneeId });
                if (result) {
                    set((s) => {
                        const next = { ...s };
                        recordActivity(next, {
                            kind: "task_assigned",
                            summary: `assigned ${result.code} → ${findMemberName(s.members, assigneeId)}`,
                            actorId: s.currentUserId,
                            targetId: id,
                        });
                        return next;
                    });
                }
                return result;
            },
            setDueDate: (id, dueDate) => get().updateTask(id, { dueDate }),

            addLabel: (id, labelId) => {
                const task = get().tasks.find((t) => t.id === id);
                if (!task || task.labelIds.includes(labelId)) return task ?? null;
                return get().updateTask(id, { labelIds: [...task.labelIds, labelId] });
            },
            removeLabel: (id, labelId) => {
                const task = get().tasks.find((t) => t.id === id);
                if (!task) return null;
                return get().updateTask(id, { labelIds: task.labelIds.filter((x) => x !== labelId) });
            },
            addToSprint: (taskId, sprintId) => get().updateTask(taskId, { sprintId }),

            batchUpdate: (ids, patch) => {
                let count = 0;
                set((s) => {
                    const idSet = new Set(ids);
                    const tasks = s.tasks.map((t) => {
                        if (!idSet.has(t.id)) return t;
                        count++;
                        return { ...t, ...patch, updatedAt: Date.now() };
                    });
                    const next = { ...s, tasks };
                    recordActivity(next, {
                        kind: "bulk_action",
                        summary: `batch-updated ${count} tasks`,
                        actorId: s.currentUserId,
                        targetId: null,
                    });
                    return next;
                });
                return count;
            },

            batchDelete: (ids) => {
                let count = 0;
                set((s) => {
                    const idSet = new Set(ids);
                    count = s.tasks.filter((t) => idSet.has(t.id)).length;
                    return {
                        tasks: s.tasks.filter((t) => !idSet.has(t.id)),
                        comments: s.comments.filter((c) => !idSet.has(c.taskId)),
                        selectedTaskIds: [],
                        drawerTaskId: s.drawerTaskId && idSet.has(s.drawerTaskId) ? null : s.drawerTaskId,
                    };
                });
                return count;
            },

            batchAddLabel: (ids, labelId) => {
                let count = 0;
                set((s) => {
                    const idSet = new Set(ids);
                    return {
                        tasks: s.tasks.map((t) => {
                            if (!idSet.has(t.id)) return t;
                            if (t.labelIds.includes(labelId)) return t;
                            count++;
                            return { ...t, labelIds: [...t.labelIds, labelId], updatedAt: Date.now() };
                        }),
                    };
                });
                return count;
            },

            batchAddToSprint: (ids, sprintId) => get().batchUpdate(ids, { sprintId }),

            addComment: (taskId, body, authorId) => {
                const comment: Comment = {
                    id: newId("c"),
                    taskId,
                    authorId: authorId ?? get().currentUserId,
                    body,
                    createdAt: Date.now(),
                };
                set((s) => {
                    const next = { ...s, comments: [...s.comments, comment] };
                    const task = s.tasks.find((t) => t.id === taskId);
                    if (task) {
                        recordActivity(next, {
                            kind: "comment_added",
                            summary: `commented on ${task.code}`,
                            actorId: comment.authorId,
                            targetId: taskId,
                        });
                    }
                    return next;
                });
                return comment;
            },

            deleteComment: (id) => {
                let removed = false;
                set((s) => {
                    const exists = s.comments.some((c) => c.id === id);
                    if (!exists) return s;
                    removed = true;
                    return { comments: s.comments.filter((c) => c.id !== id) };
                });
                return removed;
            },

            createSprint: ({ name, goal, startDate, endDate }) => {
                const now = Date.now();
                const sprint: Sprint = {
                    id: newId("sp"),
                    name,
                    goal: goal ?? "",
                    status: "planned",
                    startDate: startDate ?? now,
                    endDate: endDate ?? now + 14 * 24 * 60 * 60 * 1000,
                };
                set((s) => ({ sprints: [...s.sprints, sprint] }));
                return sprint;
            },

            updateSprint: (id, patch) => {
                let updated: Sprint | null = null;
                set((s) => ({
                    sprints: s.sprints.map((sp) => {
                        if (sp.id !== id) return sp;
                        updated = { ...sp, ...patch };
                        return updated!;
                    }),
                }));
                return updated;
            },

            setSprintStatus: (id, status) => {
                const sprint = get().updateSprint(id, { status });
                if (sprint) {
                    set((s) => {
                        const next = { ...s };
                        recordActivity(next, {
                            kind: status === "active" ? "sprint_started" : status === "completed" ? "sprint_completed" : "bulk_action",
                            summary: `${status === "active" ? "started" : status === "completed" ? "completed" : "updated"} sprint “${sprint.name}”`,
                            actorId: s.currentUserId,
                            targetId: sprint.id,
                        });
                        return next;
                    });
                }
                return sprint;
            },

            deleteSprint: (id) => {
                let removed = false;
                set((s) => {
                    const exists = s.sprints.some((sp) => sp.id === id);
                    if (!exists) return s;
                    removed = true;
                    return {
                        sprints: s.sprints.filter((sp) => sp.id !== id),
                        tasks: s.tasks.map((t) => (t.sprintId === id ? { ...t, sprintId: null } : t)),
                    };
                });
                return removed;
            },

            createMember: ({ name, role, email, capacityHours }) => {
                const member: Member = {
                    id: newId("m"),
                    name,
                    role: role ?? "Engineer",
                    email: email ?? `${name.toLowerCase().replace(/\s+/g, ".")}@example.com`,
                    capacityHours: capacityHours ?? 32,
                    color: palette[Math.floor(Math.random() * palette.length)],
                };
                set((s) => ({ members: [...s.members, member] }));
                return member;
            },

            updateMember: (id, patch) => {
                let updated: Member | null = null;
                set((s) => ({
                    members: s.members.map((m) => {
                        if (m.id !== id) return m;
                        updated = { ...m, ...patch };
                        return updated!;
                    }),
                }));
                return updated;
            },

            archiveMember: (id) => {
                let removed = false;
                set((s) => {
                    const exists = s.members.some((m) => m.id === id);
                    if (!exists) return s;
                    removed = true;
                    return {
                        members: s.members.filter((m) => m.id !== id),
                        tasks: s.tasks.map((t) => (t.assigneeId === id ? { ...t, assigneeId: null } : t)),
                    };
                });
                return removed;
            },

            createLabel: (name, color) => {
                const label: Label = {
                    id: newId("l"),
                    name,
                    color: color ?? palette[Math.floor(Math.random() * palette.length)],
                };
                set((s) => ({ labels: [...s.labels, label] }));
                return label;
            },

            deleteLabel: (id) => {
                let removed = false;
                set((s) => {
                    const exists = s.labels.some((l) => l.id === id);
                    if (!exists) return s;
                    removed = true;
                    return {
                        labels: s.labels.filter((l) => l.id !== id),
                        tasks: s.tasks.map((t) =>
                            t.labelIds.includes(id) ? { ...t, labelIds: t.labelIds.filter((x) => x !== id) } : t,
                        ),
                    };
                });
                return removed;
            },

            openDrawer: (id) => set({ drawerTaskId: id }),
            selectTask: (id, multi) =>
                set((s) => {
                    if (!multi) {
                        return { selectedTaskIds: s.selectedTaskIds.includes(id) ? [] : [id] };
                    }
                    return {
                        selectedTaskIds: s.selectedTaskIds.includes(id)
                            ? s.selectedTaskIds.filter((x) => x !== id)
                            : [...s.selectedTaskIds, id],
                    };
                }),
            clearSelection: () => set({ selectedTaskIds: [] }),
            selectAll: (ids) => set({ selectedTaskIds: ids }),

            pushToast: (message, kind) => {
                const toast: ToastItem = { id: newId("toast"), message, kind: kind ?? "info" };
                set((s) => ({ toasts: [...s.toasts, toast] }));
                setTimeout(() => {
                    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== toast.id) }));
                }, 3200);
            },

            dismissToast: (id) =>
                set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

            reset: () => {
                // No persisted store anymore — a hard reload is
                // already enough to wipe state and re-seed. Kept
                // as a Settings → Reset workspace shortcut so the
                // visitor doesn't have to know that.
                location.reload();
            },
        }),
);
