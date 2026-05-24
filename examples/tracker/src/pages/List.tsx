import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useStore } from "../store";
import type { TaskPriority, TaskStatus } from "../store/types";
import { Avatar } from "../components/Avatar";
import { LabelChip, PriorityBadge, StatusBadge } from "../components/Badges";
import { formatDate, isOverdue } from "../lib/time";

const PRIORITY_RANK: Record<TaskPriority, number> = { urgent: 0, high: 1, medium: 2, low: 3 };

export function List() {
    const tasks = useStore((s) => s.tasks);
    const members = useStore((s) => s.members);
    const labels = useStore((s) => s.labels);
    const sprints = useStore((s) => s.sprints);
    const selected = useStore((s) => s.selectedTaskIds);
    const selectTask = useStore((s) => s.selectTask);
    const selectAll = useStore((s) => s.selectAll);
    const clearSelection = useStore((s) => s.clearSelection);
    const openDrawer = useStore((s) => s.openDrawer);

    const [params] = useSearchParams();
    const [statusFilter, setStatusFilter] = useState<TaskStatus | "all">("all");
    const [priorityFilter, setPriorityFilter] = useState<TaskPriority | "all">("all");
    const [assigneeFilter, setAssigneeFilter] = useState<string>("all");
    const [sprintFilter, setSprintFilter] = useState<string>("all");
    const [labelFilter, setLabelFilter] = useState<string>("all");

    const query = params.get("q")?.toLowerCase() ?? "";

    const filtered = useMemo(() => {
        return tasks
            .filter((t) => {
                if (query && !(
                    t.title.toLowerCase().includes(query) ||
                    t.code.toLowerCase().includes(query) ||
                    t.description.toLowerCase().includes(query)
                )) return false;
                if (statusFilter !== "all" && t.status !== statusFilter) return false;
                if (priorityFilter !== "all" && t.priority !== priorityFilter) return false;
                if (assigneeFilter !== "all") {
                    if (assigneeFilter === "_unassigned" ? t.assigneeId !== null : t.assigneeId !== assigneeFilter)
                        return false;
                }
                if (sprintFilter !== "all") {
                    if (sprintFilter === "_none" ? t.sprintId !== null : t.sprintId !== sprintFilter)
                        return false;
                }
                if (labelFilter !== "all" && !t.labelIds.includes(labelFilter)) return false;
                return true;
            })
            .sort((a, b) => {
                const p = PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority];
                if (p !== 0) return p;
                return b.updatedAt - a.updatedAt;
            });
    }, [tasks, query, statusFilter, priorityFilter, assigneeFilter, sprintFilter, labelFilter]);

    const allSelected = filtered.length > 0 && filtered.every((t) => selected.includes(t.id));
    const toggleAll = () => {
        if (allSelected) clearSelection();
        else selectAll(filtered.map((t) => t.id));
    };

    return (
        <>
            <div className="page-h">
                <div>
                    <h1>Tasks</h1>
                    <p>{filtered.length} of {tasks.length} tasks · select multiple to bulk-edit.</p>
                </div>
            </div>

            <div className="list-toolbar">
                <select className="select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as any)}>
                    <option value="all">All statuses</option>
                    <option value="backlog">Backlog</option>
                    <option value="in_progress">In progress</option>
                    <option value="review">Review</option>
                    <option value="done">Done</option>
                </select>
                <select className="select" value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value as any)}>
                    <option value="all">All priorities</option>
                    <option value="urgent">Urgent</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                </select>
                <select className="select" value={assigneeFilter} onChange={(e) => setAssigneeFilter(e.target.value)}>
                    <option value="all">All assignees</option>
                    <option value="_unassigned">Unassigned</option>
                    {members.map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                </select>
                <select className="select" value={sprintFilter} onChange={(e) => setSprintFilter(e.target.value)}>
                    <option value="all">All sprints</option>
                    <option value="_none">No sprint</option>
                    {sprints.map((s) => (
                        <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                </select>
                <select className="select" value={labelFilter} onChange={(e) => setLabelFilter(e.target.value)}>
                    <option value="all">All labels</option>
                    {labels.map((l) => (
                        <option key={l.id} value={l.id}>{l.name}</option>
                    ))}
                </select>
                <span style={{ flex: 1 }} />
                {selected.length > 0 && (
                    <span className="text-xs text-muted">{selected.length} selected</span>
                )}
            </div>

            <table className="task-table">
                <thead>
                    <tr>
                        <th style={{ width: 36 }}>
                            <span
                                className={`checkbox${allSelected ? " checked" : ""}`}
                                onClick={toggleAll}
                            >
                                {allSelected ? "✓" : ""}
                            </span>
                        </th>
                        <th style={{ width: 88 }}>Code</th>
                        <th>Title</th>
                        <th style={{ width: 130 }}>Status</th>
                        <th style={{ width: 95 }}>Priority</th>
                        <th style={{ width: 130 }}>Assignee</th>
                        <th style={{ width: 100 }}>Due</th>
                        <th style={{ width: 200 }}>Labels</th>
                    </tr>
                </thead>
                <tbody>
                    {filtered.map((t) => {
                        const checked = selected.includes(t.id);
                        const assignee = members.find((m) => m.id === t.assigneeId);
                        const overdue = t.status !== "done" && isOverdue(t.dueDate);
                        return (
                            <tr
                                key={t.id}
                                className={checked ? "selected" : ""}
                                onClick={(e) => {
                                    if ((e.target as HTMLElement).closest(".checkbox")) return;
                                    openDrawer(t.id);
                                }}
                            >
                                <td>
                                    <span
                                        className={`checkbox${checked ? " checked" : ""}`}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            selectTask(t.id, true);
                                        }}
                                    >
                                        {checked ? "✓" : ""}
                                    </span>
                                </td>
                                <td className="mono text-dim">{t.code}</td>
                                <td style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</td>
                                <td><StatusBadge status={t.status} /></td>
                                <td><PriorityBadge priority={t.priority} /></td>
                                <td>
                                    {assignee ? (
                                        <span className="flex items-center gap-2">
                                            <Avatar member={assignee} />
                                            <span className="text-sm">{assignee.name.split(" ")[0]}</span>
                                        </span>
                                    ) : (
                                        <span className="text-dim">—</span>
                                    )}
                                </td>
                                <td>
                                    {t.dueDate != null ? (
                                        <span style={{ color: overdue ? "var(--danger)" : undefined }}>
                                            {formatDate(t.dueDate)}
                                        </span>
                                    ) : (
                                        <span className="text-dim">—</span>
                                    )}
                                </td>
                                <td>
                                    <span className="flex gap-2" style={{ flexWrap: "wrap" }}>
                                        {labels
                                            .filter((l) => t.labelIds.includes(l.id))
                                            .slice(0, 3)
                                            .map((l) => (
                                                <LabelChip key={l.id} label={l} />
                                            ))}
                                    </span>
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
            {filtered.length === 0 && (
                <div className="empty">
                    <div className="empty-h">No tasks match these filters</div>
                    <div>Try clearing filters or creating a new task.</div>
                </div>
            )}
        </>
    );
}
