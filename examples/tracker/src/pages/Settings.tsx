import { useState } from "react";
import { useStore } from "../store";
import { LabelChip } from "../components/Badges";

export function Settings() {
    const labels = useStore((s) => s.labels);
    const createLabel = useStore((s) => s.createLabel);
    const deleteLabel = useStore((s) => s.deleteLabel);
    const reset = useStore((s) => s.reset);
    const pushToast = useStore((s) => s.pushToast);

    const [labelName, setLabelName] = useState("");
    const [labelColor, setLabelColor] = useState("#a3b8e0");

    return (
        <>
            <div className="page-h">
                <div>
                    <h1>Settings</h1>
                    <p>Customize labels and manage workspace data.</p>
                </div>
            </div>

            <div className="card" style={{ marginBottom: 12 }}>
                <div className="drawer-section-h" style={{ margin: "0 0 12px" }}>
                    Labels
                </div>
                <div className="flex gap-2" style={{ flexWrap: "wrap", marginBottom: 14 }}>
                    {labels.map((l) => (
                        <span key={l.id} className="flex items-center gap-2">
                            <LabelChip label={l} />
                            <button
                                className="btn btn-sm btn-ghost"
                                onClick={() => {
                                    if (confirm(`Delete label “${l.name}”?`)) deleteLabel(l.id);
                                }}
                                style={{ padding: "2px 6px", fontSize: 11 }}
                            >
                                ×
                            </button>
                        </span>
                    ))}
                </div>
                <div className="flex gap-2 items-center">
                    <input
                        className="input"
                        placeholder="New label name"
                        value={labelName}
                        onChange={(e) => setLabelName(e.target.value)}
                    />
                    <input
                        type="color"
                        className="input"
                        value={labelColor}
                        onChange={(e) => setLabelColor(e.target.value)}
                        style={{ width: 50, padding: 2 }}
                    />
                    <button
                        className="btn btn-primary"
                        onClick={() => {
                            if (!labelName.trim()) return;
                            const l = createLabel(labelName.trim(), labelColor);
                            pushToast(`Created label “${l.name}”`, "success");
                            setLabelName("");
                        }}
                    >
                        Add label
                    </button>
                </div>
            </div>

            <div className="card">
                <div className="drawer-section-h" style={{ margin: "0 0 12px" }}>
                    Workspace data
                </div>
                <p className="text-sm text-muted">
                    All data lives in your browser's local storage — no server, no telemetry. Reset to wipe and reseed with the defaults.
                </p>
                <button className="btn btn-danger mt-3" onClick={() => {
                    if (confirm("Wipe all local data and reload?")) reset();
                }}>
                    Reset workspace
                </button>
            </div>
        </>
    );
}
