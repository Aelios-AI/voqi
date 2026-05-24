export type TaskStatus = "backlog" | "in_progress" | "review" | "done";
export type TaskPriority = "urgent" | "high" | "medium" | "low";

export interface Label {
    id: string;
    name: string;
    color: string;
}

export interface Comment {
    id: string;
    taskId: string;
    authorId: string;
    body: string;
    createdAt: number;
}

export interface Task {
    id: string;
    code: string;
    title: string;
    description: string;
    status: TaskStatus;
    priority: TaskPriority;
    assigneeId: string | null;
    labelIds: string[];
    sprintId: string | null;
    dueDate: number | null;
    estimateHours: number | null;
    createdAt: number;
    updatedAt: number;
}

export interface Member {
    id: string;
    name: string;
    role: string;
    email: string;
    capacityHours: number;
    color: string;
}

export type SprintStatus = "planned" | "active" | "completed";

export interface Sprint {
    id: string;
    name: string;
    goal: string;
    status: SprintStatus;
    startDate: number;
    endDate: number;
}

export interface ActivityItem {
    id: string;
    kind:
        | "task_created"
        | "task_moved"
        | "task_assigned"
        | "task_completed"
        | "comment_added"
        | "sprint_started"
        | "sprint_completed"
        | "bulk_action";
    summary: string;
    actorId: string;
    targetId: string | null;
    createdAt: number;
}
