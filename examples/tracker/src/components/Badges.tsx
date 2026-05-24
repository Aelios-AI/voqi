import type { Label, TaskPriority, TaskStatus } from "../store/types";

const STATUS_DOT: Record<TaskStatus, string> = {
    backlog: "#5a5a62",
    in_progress: "#5b7cb7",
    review: "#b8924c",
    done: "#6b9b73",
};

const STATUS_LABEL: Record<TaskStatus, string> = {
    backlog: "Backlog",
    in_progress: "In progress",
    review: "Review",
    done: "Done",
};

export function StatusBadge({ status }: { status: TaskStatus }) {
    return (
        <span className="badge" style={{ color: STATUS_DOT[status] }}>
            <span className="badge-dot" />
            <span style={{ color: "var(--text-muted)" }}>{STATUS_LABEL[status]}</span>
        </span>
    );
}

export function PriorityBadge({ priority }: { priority: TaskPriority }) {
    return (
        <span className={`badge priority-${priority}`} title={`Priority: ${priority}`}>
            <span className="badge-dot" />
            <span style={{ color: "var(--text-muted)", textTransform: "capitalize" }}>
                {priority}
            </span>
        </span>
    );
}

export function LabelChip({ label }: { label: Label }) {
    return (
        <span className="label-chip">
            <span className="label-chip-dot" style={{ background: label.color }} />
            {label.name}
        </span>
    );
}
