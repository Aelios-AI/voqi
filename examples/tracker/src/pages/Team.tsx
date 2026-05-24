import { useState } from "react";
import { useStore } from "../store";
import { Avatar } from "../components/Avatar";

export function Team() {
    const members = useStore((s) => s.members);
    const tasks = useStore((s) => s.tasks);
    const createMember = useStore((s) => s.createMember);
    const archiveMember = useStore((s) => s.archiveMember);
    const pushToast = useStore((s) => s.pushToast);

    const [adding, setAdding] = useState(false);
    const [name, setName] = useState("");
    const [role, setRole] = useState("Engineer");
    const [email, setEmail] = useState("");

    return (
        <>
            <div className="page-h">
                <div>
                    <h1>Team</h1>
                    <p>{members.length} people · capacity for the current sprint.</p>
                </div>
                <div className="page-h-actions">
                    {!adding && (
                        <button className="btn btn-primary" onClick={() => setAdding(true)}>
                            + Add member
                        </button>
                    )}
                </div>
            </div>

            {adding && (
                <div className="card" style={{ marginBottom: 16 }}>
                    <div className="drawer-section-h" style={{ margin: "0 0 8px" }}>
                        New member
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        <input className="input" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
                        <input className="input" placeholder="Role" value={role} onChange={(e) => setRole(e.target.value)} />
                        <input className="input" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
                        <div className="flex gap-2" style={{ justifyContent: "flex-end" }}>
                            <button
                                className="btn btn-ghost"
                                onClick={() => {
                                    setAdding(false);
                                    setName("");
                                    setEmail("");
                                }}
                            >
                                Cancel
                            </button>
                            <button
                                className="btn btn-primary"
                                disabled={!name.trim()}
                                onClick={() => {
                                    const m = createMember({ name: name.trim(), role, email: email || undefined });
                                    pushToast(`Added ${m.name}`, "success");
                                    setName("");
                                    setEmail("");
                                    setAdding(false);
                                }}
                            >
                                Add member
                            </button>
                        </div>
                    </div>
                </div>
            )}

            <div className="team-grid">
                {members.map((m) => {
                    const open = tasks.filter((t) => t.assigneeId === m.id && t.status !== "done");
                    const inProgress = open.filter((t) => t.status === "in_progress").length;
                    const estimateLoad = open.reduce((sum, t) => sum + (t.estimateHours ?? 0), 0);
                    const loadPct = m.capacityHours
                        ? Math.min(100, Math.round((estimateLoad / m.capacityHours) * 100))
                        : 0;
                    return (
                        <div key={m.id} className="card team-card">
                            <div className="team-card-h">
                                <Avatar member={m} size="lg" />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div className="team-card-name">{m.name}</div>
                                    <div className="team-card-role">{m.role}</div>
                                </div>
                            </div>
                            <div>
                                <div className="team-stat">
                                    <span>Open tasks</span>
                                    <strong>{open.length}</strong>
                                </div>
                                <div className="team-stat">
                                    <span>In progress</span>
                                    <strong>{inProgress}</strong>
                                </div>
                                <div className="team-stat">
                                    <span>Capacity</span>
                                    <strong>{m.capacityHours}h / wk</strong>
                                </div>
                                <div className="team-stat">
                                    <span>Estimated load</span>
                                    <strong style={{ color: loadPct > 90 ? "var(--danger)" : loadPct > 70 ? "var(--warn)" : undefined }}>
                                        {estimateLoad}h ({loadPct}%)
                                    </strong>
                                </div>
                                <div className="sprint-progress mt-2" style={{ width: "100%" }}>
                                    <div
                                        className="sprint-progress-bar"
                                        style={{
                                            width: `${loadPct}%`,
                                            background: loadPct > 90 ? "var(--danger)" : loadPct > 70 ? "var(--warn)" : "var(--text)",
                                        }}
                                    />
                                </div>
                            </div>
                            <div className="flex gap-2" style={{ marginTop: 8 }}>
                                <button
                                    className="btn btn-sm btn-danger"
                                    onClick={() => {
                                        if (confirm(`Remove ${m.name}?`)) archiveMember(m.id);
                                    }}
                                >
                                    Remove
                                </button>
                            </div>
                        </div>
                    );
                })}
            </div>
        </>
    );
}
