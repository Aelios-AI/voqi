import { useState } from "react";
import { useStore } from "../store";
import type { Task, TaskStatus } from "../store/types";
import { Avatar } from "../components/Avatar";
import { LabelChip, PriorityBadge } from "../components/Badges";
import { formatDate, isOverdue } from "../lib/time";

const COLUMNS: { key: TaskStatus; label: string }[] = [
    { key: "backlog", label: "Backlog" },
    { key: "in_progress", label: "In progress" },
    { key: "review", label: "Review" },
    { key: "done", label: "Done" },
];

export function Board() {
    const tasks = useStore((s) => s.tasks);
    const labels = useStore((s) => s.labels);
    const members = useStore((s) => s.members);
    const moveTask = useStore((s) => s.moveTask);
    const openDrawer = useStore((s) => s.openDrawer);
    const [draggingId, setDraggingId] = useState<string | null>(null);

    const grouped: Record<TaskStatus, Task[]> = {
        backlog: [], in_progress: [], review: [], done: [],
    };
    for (const t of tasks) grouped[t.status].push(t);

    return (
        <>
            <div className="page-h">
                <div>
                    <h1>Board</h1>
                    <p>Drag tasks across columns or use the bulk bar from the list view.</p>
                </div>
            </div>

            <div className="board">
                {COLUMNS.map((col) => (
                    <div
                        className="board-col"
                        key={col.key}
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={() => {
                            if (draggingId) {
                                moveTask(draggingId, col.key);
                                setDraggingId(null);
                            }
                        }}
                    >
                        <div className="board-col-h">
                            <span>{col.label}</span>
                            <span className="board-col-h-count">{grouped[col.key].length}</span>
                        </div>
                        <div className="board-col-body">
                            {grouped[col.key]
                                .sort((a, b) => {
                                    const order = { urgent: 0, high: 1, medium: 2, low: 3 } as const;
                                    return order[a.priority] - order[b.priority];
                                })
                                .map((t) => {
                                    const assignee = members.find((m) => m.id === t.assigneeId);
                                    const taskLabels = labels.filter((l) => t.labelIds.includes(l.id));
                                    const overdue = t.status !== "done" && isOverdue(t.dueDate);
                                    return (
                                        <div
                                            key={t.id}
                                            className={`task-card${draggingId === t.id ? " dragging" : ""}`}
                                            draggable
                                            onDragStart={() => setDraggingId(t.id)}
                                            onDragEnd={() => setDraggingId(null)}
                                            onClick={() => openDrawer(t.id)}
                                        >
                                            <div className="flex items-center" style={{ justifyContent: "space-between" }}>
                                                <span className="task-card-id mono">{t.code}</span>
                                                <PriorityBadge priority={t.priority} />
                                            </div>
                                            <div className="task-card-title">{t.title}</div>
                                            {taskLabels.length > 0 && (
                                                <div className="flex gap-2" style={{ flexWrap: "wrap" }}>
                                                    {taskLabels.map((l) => (
                                                        <LabelChip key={l.id} label={l} />
                                                    ))}
                                                </div>
                                            )}
                                            <div className="task-card-meta">
                                                {t.dueDate != null && (
                                                    <span
                                                        className="text-xs"
                                                        style={{ color: overdue ? "var(--danger)" : "var(--text-muted)" }}
                                                    >
                                                        {overdue ? "Overdue · " : "Due "}
                                                        {formatDate(t.dueDate)}
                                                    </span>
                                                )}
                                                <span style={{ flex: 1 }} />
                                                <Avatar member={assignee} />
                                            </div>
                                        </div>
                                    );
                                })}
                            {grouped[col.key].length === 0 && (
                                <div className="text-xs text-dim" style={{ padding: 12, textAlign: "center" }}>
                                    No tasks
                                </div>
                            )}
                        </div>
                    </div>
                ))}
            </div>
        </>
    );
}
