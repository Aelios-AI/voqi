import { useStore } from "../store";
import { Avatar } from "../components/Avatar";
import { formatRelative } from "../lib/time";

export function Inbox() {
    const comments = useStore((s) => s.comments);
    const tasks = useStore((s) => s.tasks);
    const members = useStore((s) => s.members);
    const activity = useStore((s) => s.activity);
    const openDrawer = useStore((s) => s.openDrawer);

    const recentComments = [...comments].sort((a, b) => b.createdAt - a.createdAt).slice(0, 20);

    return (
        <>
            <div className="page-h">
                <div>
                    <h1>Inbox</h1>
                    <p>Comments, mentions, and recent activity from across the workspace.</p>
                </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 12 }}>
                <div className="card">
                    <div className="drawer-section-h" style={{ margin: "0 0 12px" }}>
                        Recent comments
                    </div>
                    {recentComments.length === 0 ? (
                        <div className="empty">No comments yet.</div>
                    ) : (
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                            {recentComments.map((c) => {
                                const task = tasks.find((t) => t.id === c.taskId);
                                const author = members.find((m) => m.id === c.authorId);
                                return (
                                    <button
                                        key={c.id}
                                        onClick={() => task && openDrawer(task.id)}
                                        style={{
                                            textAlign: "left",
                                            padding: 12,
                                            background: "var(--panel-2)",
                                            border: "1px solid var(--border)",
                                            borderRadius: 8,
                                            cursor: "pointer",
                                        }}
                                    >
                                        <div className="flex items-center gap-2">
                                            <Avatar member={author} />
                                            <span className="text-sm font-semibold">{author?.name ?? "Unknown"}</span>
                                            <span className="text-xs text-dim">on</span>
                                            <span className="mono text-xs text-dim">{task?.code}</span>
                                            <span className="text-xs text-dim mono" style={{ marginLeft: "auto" }}>
                                                {formatRelative(c.createdAt)}
                                            </span>
                                        </div>
                                        <div className="text-sm mt-2" style={{ color: "var(--text-muted)" }}>
                                            {c.body}
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                    )}
                </div>

                <div className="card">
                    <div className="drawer-section-h" style={{ margin: "0 0 12px" }}>
                        Activity feed
                    </div>
                    {activity.length === 0 ? (
                        <div className="empty">No activity yet.</div>
                    ) : (
                        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {activity.slice(0, 24).map((a) => {
                                const actor = members.find((m) => m.id === a.actorId);
                                const task = a.targetId ? tasks.find((t) => t.id === a.targetId) : null;
                                return (
                                    <div
                                        key={a.id}
                                        className="flex items-center gap-2"
                                        style={{
                                            padding: "6px 4px",
                                            cursor: task ? "pointer" : undefined,
                                        }}
                                        onClick={() => task && openDrawer(task.id)}
                                    >
                                        <Avatar member={actor} />
                                        <span className="text-sm" style={{ flex: 1, color: "var(--text-muted)" }}>
                                            <strong style={{ color: "var(--text)" }}>{actor?.name.split(" ")[0]}</strong>{" "}
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
        </>
    );
}
