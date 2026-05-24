import { useEffect, useState } from "react";
import { useStore } from "../store";
import type { TaskPriority, TaskStatus } from "../store/types";
import { formatDate, formatRelative } from "../lib/time";
import { Avatar } from "./Avatar";
import { LabelChip, PriorityBadge, StatusBadge } from "./Badges";

const STATUS_OPTIONS: TaskStatus[] = ["backlog", "in_progress", "review", "done"];
const PRIORITY_OPTIONS: TaskPriority[] = ["urgent", "high", "medium", "low"];

export function TaskDrawer() {
    const taskId = useStore((s) => s.drawerTaskId);
    const task = useStore((s) => s.tasks.find((t) => t.id === taskId));
    const labels = useStore((s) => s.labels);
    const members = useStore((s) => s.members);
    const sprints = useStore((s) => s.sprints);
    const comments = useStore((s) => s.comments.filter((c) => c.taskId === taskId));
    const openDrawer = useStore((s) => s.openDrawer);
    const updateTask = useStore((s) => s.updateTask);
    const moveTask = useStore((s) => s.moveTask);
    const setPriority = useStore((s) => s.setPriority);
    const assignTask = useStore((s) => s.assignTask);
    const addLabel = useStore((s) => s.addLabel);
    const removeLabel = useStore((s) => s.removeLabel);
    const addToSprint = useStore((s) => s.addToSprint);
    const addComment = useStore((s) => s.addComment);
    const deleteTask = useStore((s) => s.deleteTask);

    const [draftTitle, setDraftTitle] = useState("");
    const [draftDesc, setDraftDesc] = useState("");
    const [draftComment, setDraftComment] = useState("");

    useEffect(() => {
        if (task) {
            setDraftTitle(task.title);
            setDraftDesc(task.description);
        }
    }, [task?.id]);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") openDrawer(null);
        };
        if (taskId) {
            window.addEventListener("keydown", onKey);
            return () => window.removeEventListener("keydown", onKey);
        }
    }, [taskId, openDrawer]);

    if (!taskId || !task) return null;

    const sprint = sprints.find((s) => s.id === task.sprintId);
    const assignee = members.find((m) => m.id === task.assigneeId);

    const commitTitle = () => {
        if (draftTitle.trim() && draftTitle !== task.title) {
            updateTask(task.id, { title: draftTitle.trim() });
        }
    };
    const commitDesc = () => {
        if (draftDesc !== task.description) {
            updateTask(task.id, { description: draftDesc });
        }
    };

    return (
        <>
            <div className="drawer-bg" onClick={() => openDrawer(null)} />
            <div className="drawer">
                <div className="drawer-h">
                    <span className="drawer-h-id mono">{task.code}</span>
                    <div className="flex gap-2 items-center">
                        <button
                            className="btn btn-sm btn-danger"
                            onClick={() => {
                                if (confirm(`Delete ${task.code}?`)) {
                                    deleteTask(task.id);
                                }
                            }}
                        >
                            Delete
                        </button>
                        <button className="btn btn-sm btn-ghost" onClick={() => openDrawer(null)}>
                            Close
                        </button>
                    </div>
                </div>

                <div className="drawer-body">
                    <input
                        className="drawer-title-input"
                        value={draftTitle}
                        onChange={(e) => setDraftTitle(e.target.value)}
                        onBlur={commitTitle}
                        onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLInputElement).blur()}
                    />

                    <div className="drawer-meta-grid">
                        <span className="label">Status</span>
                        <div>
                            <select
                                className="select"
                                value={task.status}
                                onChange={(e) => moveTask(task.id, e.target.value as TaskStatus)}
                            >
                                {STATUS_OPTIONS.map((s) => (
                                    <option key={s} value={s}>
                                        {s.replace("_", " ")}
                                    </option>
                                ))}
                            </select>
                            <span style={{ marginLeft: 8 }}>
                                <StatusBadge status={task.status} />
                            </span>
                        </div>

                        <span className="label">Priority</span>
                        <div>
                            <select
                                className="select"
                                value={task.priority}
                                onChange={(e) => setPriority(task.id, e.target.value as TaskPriority)}
                            >
                                {PRIORITY_OPTIONS.map((p) => (
                                    <option key={p} value={p}>
                                        {p}
                                    </option>
                                ))}
                            </select>
                            <span style={{ marginLeft: 8 }}>
                                <PriorityBadge priority={task.priority} />
                            </span>
                        </div>

                        <span className="label">Assignee</span>
                        <div className="flex items-center gap-2">
                            <Avatar member={assignee} />
                            <select
                                className="select"
                                value={task.assigneeId ?? ""}
                                onChange={(e) => assignTask(task.id, e.target.value || null)}
                            >
                                <option value="">— Unassigned —</option>
                                {members.map((m) => (
                                    <option key={m.id} value={m.id}>
                                        {m.name}
                                    </option>
                                ))}
                            </select>
                        </div>

                        <span className="label">Sprint</span>
                        <select
                            className="select"
                            value={task.sprintId ?? ""}
                            onChange={(e) => addToSprint(task.id, e.target.value || null)}
                        >
                            <option value="">— No sprint —</option>
                            {sprints.map((s) => (
                                <option key={s.id} value={s.id}>
                                    {s.name}
                                </option>
                            ))}
                        </select>

                        <span className="label">Due</span>
                        <input
                            type="date"
                            className="input"
                            value={task.dueDate ? new Date(task.dueDate).toISOString().slice(0, 10) : ""}
                            onChange={(e) =>
                                updateTask(task.id, {
                                    dueDate: e.target.value ? new Date(e.target.value).getTime() : null,
                                })
                            }
                        />

                        <span className="label">Estimate</span>
                        <div>
                            <input
                                type="number"
                                className="input"
                                style={{ width: 80 }}
                                value={task.estimateHours ?? ""}
                                onChange={(e) =>
                                    updateTask(task.id, {
                                        estimateHours: e.target.value ? parseInt(e.target.value, 10) : null,
                                    })
                                }
                            />
                            <span className="text-muted text-xs" style={{ marginLeft: 8 }}>
                                hours
                            </span>
                        </div>
                    </div>

                    <div>
                        <div className="drawer-section-h">Labels</div>
                        <div className="flex gap-2" style={{ flexWrap: "wrap" }}>
                            {task.labelIds.map((lid) => {
                                const l = labels.find((x) => x.id === lid);
                                if (!l) return null;
                                return (
                                    <button
                                        key={l.id}
                                        className="label-chip"
                                        title="Click to remove"
                                        onClick={() => removeLabel(task.id, l.id)}
                                    >
                                        <span className="label-chip-dot" style={{ background: l.color }} />
                                        {l.name}
                                        <span style={{ color: "var(--text-dim)", marginLeft: 4 }}>×</span>
                                    </button>
                                );
                            })}
                            <select
                                className="select"
                                value=""
                                onChange={(e) => {
                                    if (e.target.value) addLabel(task.id, e.target.value);
                                }}
                                style={{ fontSize: 11 }}
                            >
                                <option value="">+ add label</option>
                                {labels
                                    .filter((l) => !task.labelIds.includes(l.id))
                                    .map((l) => (
                                        <option key={l.id} value={l.id}>
                                            {l.name}
                                        </option>
                                    ))}
                            </select>
                        </div>
                    </div>

                    <div>
                        <div className="drawer-section-h">Description</div>
                        <textarea
                            className="comment-input"
                            placeholder="Add some context…"
                            value={draftDesc}
                            onChange={(e) => setDraftDesc(e.target.value)}
                            onBlur={commitDesc}
                        />
                    </div>

                    <div>
                        <div className="drawer-section-h">Activity ({comments.length})</div>
                        <div>
                            {comments.map((c) => {
                                const author = members.find((m) => m.id === c.authorId);
                                return (
                                    <div className="comment" key={c.id}>
                                        <div className="comment-head">
                                            <Avatar member={author} />
                                            <span className="comment-author">{author?.name ?? "Unknown"}</span>
                                            <span className="comment-time">{formatRelative(c.createdAt)}</span>
                                        </div>
                                        <div className="comment-body">{c.body}</div>
                                    </div>
                                );
                            })}
                        </div>
                        <textarea
                            className="comment-input"
                            placeholder="Leave a comment…"
                            value={draftComment}
                            onChange={(e) => setDraftComment(e.target.value)}
                            onKeyDown={(e) => {
                                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                                    if (draftComment.trim()) {
                                        addComment(task.id, draftComment.trim());
                                        setDraftComment("");
                                    }
                                }
                            }}
                        />
                        <div className="flex gap-2 mt-2" style={{ justifyContent: "flex-end" }}>
                            <button
                                className="btn btn-sm btn-primary"
                                disabled={!draftComment.trim()}
                                onClick={() => {
                                    if (draftComment.trim()) {
                                        addComment(task.id, draftComment.trim());
                                        setDraftComment("");
                                    }
                                }}
                            >
                                Comment
                            </button>
                        </div>
                    </div>

                    <div className="text-xs text-dim mono">
                        Created {formatDate(task.createdAt)} · Updated {formatRelative(task.updatedAt)}
                    </div>
                </div>
            </div>
        </>
    );
}
