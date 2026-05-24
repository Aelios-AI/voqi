import { useStore } from "../store";
import type { TaskPriority, TaskStatus } from "../store/types";

export function BulkBar() {
    const ids = useStore((s) => s.selectedTaskIds);
    const members = useStore((s) => s.members);
    const labels = useStore((s) => s.labels);
    const sprints = useStore((s) => s.sprints);
    const batchUpdate = useStore((s) => s.batchUpdate);
    const batchAddLabel = useStore((s) => s.batchAddLabel);
    const batchDelete = useStore((s) => s.batchDelete);
    const clearSelection = useStore((s) => s.clearSelection);
    const pushToast = useStore((s) => s.pushToast);

    if (ids.length === 0) return null;

    return (
        <div className="bulk-bar">
            <span className="bulk-bar-count">{ids.length} selected</span>
            <span className="bulk-bar-divider" />
            <select
                className="select"
                value=""
                onChange={(e) => {
                    if (!e.target.value) return;
                    const count = batchUpdate(ids, { status: e.target.value as TaskStatus });
                    pushToast(`Moved ${count} → ${e.target.value.replace("_", " ")}`, "success");
                    e.currentTarget.value = "";
                }}
            >
                <option value="">Move to…</option>
                <option value="backlog">Backlog</option>
                <option value="in_progress">In progress</option>
                <option value="review">Review</option>
                <option value="done">Done</option>
            </select>
            <select
                className="select"
                value=""
                onChange={(e) => {
                    if (!e.target.value) return;
                    const count = batchUpdate(ids, { priority: e.target.value as TaskPriority });
                    pushToast(`Set priority on ${count}`, "success");
                    e.currentTarget.value = "";
                }}
            >
                <option value="">Set priority…</option>
                <option value="urgent">Urgent</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
            </select>
            <select
                className="select"
                value=""
                onChange={(e) => {
                    const v = e.target.value;
                    if (!v) return;
                    const count = batchUpdate(ids, { assigneeId: v === "_unassigned" ? null : v });
                    pushToast(`Reassigned ${count} tasks`, "success");
                    e.currentTarget.value = "";
                }}
            >
                <option value="">Assign to…</option>
                <option value="_unassigned">— Unassigned —</option>
                {members.map((m) => (
                    <option key={m.id} value={m.id}>
                        {m.name}
                    </option>
                ))}
            </select>
            <select
                className="select"
                value=""
                onChange={(e) => {
                    if (!e.target.value) return;
                    const count = batchAddLabel(ids, e.target.value);
                    pushToast(`Tagged ${count} tasks`, "success");
                    e.currentTarget.value = "";
                }}
            >
                <option value="">Add label…</option>
                {labels.map((l) => (
                    <option key={l.id} value={l.id}>
                        {l.name}
                    </option>
                ))}
            </select>
            <select
                className="select"
                value=""
                onChange={(e) => {
                    const v = e.target.value;
                    if (!v) return;
                    const count = batchUpdate(ids, { sprintId: v === "_none" ? null : v });
                    pushToast(`Moved ${count} tasks to sprint`, "success");
                    e.currentTarget.value = "";
                }}
            >
                <option value="">Sprint…</option>
                <option value="_none">— No sprint —</option>
                {sprints.map((s) => (
                    <option key={s.id} value={s.id}>
                        {s.name}
                    </option>
                ))}
            </select>
            <span className="bulk-bar-divider" />
            <button
                className="btn btn-sm btn-danger"
                onClick={() => {
                    if (confirm(`Delete ${ids.length} tasks?`)) {
                        const count = batchDelete(ids);
                        pushToast(`Deleted ${count} tasks`, "info");
                    }
                }}
            >
                Delete
            </button>
            <button className="btn btn-sm btn-ghost" onClick={clearSelection}>
                Clear
            </button>
        </div>
    );
}
