import { useStore } from "../store";
import { formatRelative, isOverdue } from "../lib/time";
import { Avatar } from "../components/Avatar";
import { PriorityBadge, StatusBadge } from "../components/Badges";

export function Dashboard() {
    const tasks = useStore((s) => s.tasks);
    const sprints = useStore((s) => s.sprints);
    const members = useStore((s) => s.members);
    const me = useStore((s) => s.members.find((m) => m.id === s.currentUserId));
    const activity = useStore((s) => s.activity);
    const openDrawer = useStore((s) => s.openDrawer);

    const activeSprint = sprints.find((s) => s.status === "active");
    const sprintTasks = activeSprint ? tasks.filter((t) => t.sprintId === activeSprint.id) : [];
    const sprintDone = sprintTasks.filter((t) => t.status === "done").length;
    const sprintProgress = sprintTasks.length ? (sprintDone / sprintTasks.length) * 100 : 0;

    const myTasks = tasks.filter((t) => t.assigneeId === me?.id && t.status !== "done");
    const myOverdue = myTasks.filter((t) => isOverdue(t.dueDate));
    const inProgressCount = tasks.filter((t) => t.status === "in_progress").length;
    const reviewCount = tasks.filter((t) => t.status === "review").length;

    return (
        <>
            <div className="page-h">
                <div>
                    <h1>Welcome back, {me?.name.split(" ")[0]}</h1>
                    <p>Here's what's happening across your workspace today.</p>
                </div>
            </div>

            <div className="stat-grid">
                <div className="stat">
                    <div className="stat-label">Active sprint</div>
                    <div className="stat-value">{Math.round(sprintProgress)}%</div>
                    <div className="stat-trend">
                        {sprintDone}/{sprintTasks.length} tasks done
                    </div>
                </div>
                <div className="stat">
                    <div className="stat-label">In progress</div>
                    <div className="stat-value">{inProgressCount}</div>
                    <div className="stat-trend">across the workspace</div>
                </div>
                <div className="stat">
                    <div className="stat-label">In review</div>
                    <div className="stat-value">{reviewCount}</div>
                    <div className="stat-trend">awaiting feedback</div>
                </div>
                <div className="stat">
                    <div className="stat-label">My open tasks</div>
                    <div className="stat-value">{myTasks.length}</div>
                    <div className={`stat-trend ${myOverdue.length > 0 ? "down" : ""}`}>
                        {myOverdue.length > 0 ? `${myOverdue.length} overdue` : "on track"}
                    </div>
                </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 12 }}>
                <div className="card">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                        <div className="drawer-section-h" style={{ margin: 0 }}>
                            My tasks
                        </div>
                        <span className="text-xs text-muted">{myTasks.length} open</span>
                    </div>
                    {myTasks.length === 0 ? (
                        <div className="empty">
                            <div className="empty-h">All clear</div>
                            <div>You don't have any open tasks. Nice.</div>
                        </div>
                    ) : (
                        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {myTasks.slice(0, 8).map((t) => (
                                <button
                                    key={t.id}
                                    onClick={() => openDrawer(t.id)}
                                    style={{
                                        display: "grid",
                                        gridTemplateColumns: "60px 1fr auto auto",
                                        alignItems: "center",
                                        gap: 10,
                                        padding: "9px 12px",
                                        background: "var(--panel-2)",
                                        border: "1px solid var(--border)",
                                        borderRadius: 8,
                                        textAlign: "left",
                                        cursor: "pointer",
                                    }}
                                >
                                    <span className="mono text-xs text-dim">{t.code}</span>
                                    <span className="text-sm" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                        {t.title}
                                    </span>
                                    <PriorityBadge priority={t.priority} />
                                    <StatusBadge status={t.status} />
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                <div className="card">
                    <div className="drawer-section-h" style={{ margin: "0 0 12px" }}>
                        Recent activity
                    </div>
                    {activity.length === 0 ? (
                        <div className="empty">
                            <div>No recent activity yet.</div>
                            <div className="text-xs text-dim mt-2">Try moving a task on the board.</div>
                        </div>
                    ) : (
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                            {activity.slice(0, 12).map((a) => {
                                const actor = members.find((m) => m.id === a.actorId);
                                return (
                                    <div key={a.id} className="flex items-center gap-2" style={{ padding: "4px 0" }}>
                                        <Avatar member={actor} />
                                        <span className="text-sm" style={{ flex: 1, color: "var(--text-muted)" }}>
                                            <strong style={{ color: "var(--text)" }}>{actor?.name.split(" ")[0] ?? "Someone"}</strong>{" "}
                                            {a.summary}
                                        </span>
                                        <span className="text-xs text-dim">{formatRelative(a.createdAt)}</span>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            </div>

            {activeSprint && (
                <div className="card mt-3">
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
                        <div>
                            <div className="drawer-section-h" style={{ margin: "0 0 4px" }}>
                                Active sprint
                            </div>
                            <div style={{ fontSize: 16, fontWeight: 600 }}>{activeSprint.name}</div>
                            <div className="text-xs text-muted mt-1">{activeSprint.goal}</div>
                        </div>
                        <div style={{ textAlign: "right" }}>
                            <div className="text-xs text-muted">Ends {formatRelative(activeSprint.endDate)}</div>
                            <div className="sprint-progress mt-2" style={{ marginLeft: "auto" }}>
                                <div className="sprint-progress-bar" style={{ width: `${sprintProgress}%` }} />
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}
