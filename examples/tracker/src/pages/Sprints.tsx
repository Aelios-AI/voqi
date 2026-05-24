import { useState } from "react";
import { useStore } from "../store";
import type { SprintStatus } from "../store/types";
import { formatDate, formatRelative } from "../lib/time";

const ORDER: Record<SprintStatus, number> = { active: 0, planned: 1, completed: 2 };

export function Sprints() {
    const sprints = useStore((s) => s.sprints);
    const tasks = useStore((s) => s.tasks);
    const setSprintStatus = useStore((s) => s.setSprintStatus);
    const createSprint = useStore((s) => s.createSprint);
    const deleteSprint = useStore((s) => s.deleteSprint);
    const pushToast = useStore((s) => s.pushToast);

    const [creating, setCreating] = useState(false);
    const [name, setName] = useState("");
    const [goal, setGoal] = useState("");

    const sorted = [...sprints].sort((a, b) => ORDER[a.status] - ORDER[b.status]);

    return (
        <>
            <div className="page-h">
                <div>
                    <h1>Sprints</h1>
                    <p>Plan, run, and look back on iteration cycles.</p>
                </div>
                <div className="page-h-actions">
                    {!creating && (
                        <button className="btn btn-primary" onClick={() => setCreating(true)}>
                            + New sprint
                        </button>
                    )}
                </div>
            </div>

            {creating && (
                <div className="card mt-3" style={{ marginBottom: 16 }}>
                    <div className="drawer-section-h" style={{ margin: "0 0 8px" }}>
                        New sprint
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        <input
                            className="input"
                            placeholder="Sprint name (e.g. Sprint 25 — Search)"
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                        />
                        <input
                            className="input"
                            placeholder="Goal (one sentence)"
                            value={goal}
                            onChange={(e) => setGoal(e.target.value)}
                        />
                        <div className="flex gap-2" style={{ justifyContent: "flex-end" }}>
                            <button
                                className="btn btn-ghost"
                                onClick={() => {
                                    setCreating(false);
                                    setName("");
                                    setGoal("");
                                }}
                            >
                                Cancel
                            </button>
                            <button
                                className="btn btn-primary"
                                disabled={!name.trim()}
                                onClick={() => {
                                    if (!name.trim()) return;
                                    const s = createSprint({ name: name.trim(), goal: goal.trim() });
                                    pushToast(`Created ${s.name}`, "success");
                                    setName("");
                                    setGoal("");
                                    setCreating(false);
                                }}
                            >
                                Create sprint
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <div className="sprint-list">
                {sorted.map((sp) => {
                    const sprintTasks = tasks.filter((t) => t.sprintId === sp.id);
                    const done = sprintTasks.filter((t) => t.status === "done").length;
                    const pct = sprintTasks.length ? (done / sprintTasks.length) * 100 : 0;
                    return (
                        <div key={sp.id} className="card sprint-card">
                            <div>
                                <div className="sprint-card-h">
                                    <span className="sprint-name">{sp.name}</span>
                                    <span className={`sprint-status ${sp.status}`}>{sp.status}</span>
                                </div>
                                <div className="sprint-meta">{sp.goal}</div>
                                <div className="sprint-meta mt-2">
                                    {formatDate(sp.startDate)} → {formatDate(sp.endDate)} ·{" "}
                                    {sp.status === "active" ? `ends ${formatRelative(sp.endDate)}` : sp.status}
                                </div>
                                <div className="sprint-progress">
                                    <div className="sprint-progress-bar" style={{ width: `${pct}%` }} />
                                </div>
                                <div className="text-xs text-muted mt-2">
                                    {done}/{sprintTasks.length} tasks done · {Math.round(pct)}%
                                </div>
                            </div>
                            <div className="flex gap-2 items-center">
                                {sp.status === "planned" && (
                                    <button
                                        className="btn btn-primary btn-sm"
                                        onClick={() => setSprintStatus(sp.id, "active")}
                                    >
                                        Start sprint
                                    </button>
                                )}
                                {sp.status === "active" && (
                                    <button
                                        className="btn btn-sm"
                                        onClick={() => setSprintStatus(sp.id, "completed")}
                                    >
                                        Complete
                                    </button>
                                )}
                                <button
                                    className="btn btn-sm btn-danger"
                                    onClick={() => {
                                        if (confirm(`Delete ${sp.name}?`)) deleteSprint(sp.id);
                                    }}
                                >
                                    Delete
                                </button>
                            </div>
                        </div>
                    );
                })}
            </div>
        </>
    );
}
