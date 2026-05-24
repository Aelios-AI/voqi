import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useStore } from "../store";

const TITLE: Record<string, string> = {
    "/": "Dashboard",
    "/board": "Board",
    "/list": "Tasks",
    "/sprints": "Sprints",
    "/team": "Team",
    "/inbox": "Inbox",
    "/settings": "Settings",
};

export function Topbar() {
    const location = useLocation();
    const navigate = useNavigate();
    const [query, setQuery] = useState("");
    const tasks = useStore((s) => s.tasks);
    const openDrawer = useStore((s) => s.openDrawer);

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "k") {
                e.preventDefault();
                document.getElementById("global-search")?.focus();
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, []);

    const title = TITLE[location.pathname] ?? "Example Tracker";

    const submitSearch = () => {
        const q = query.trim().toLowerCase();
        if (!q) return;
        const hit = tasks.find(
            (t) =>
                t.code.toLowerCase() === q ||
                t.title.toLowerCase().includes(q) ||
                t.description.toLowerCase().includes(q),
        );
        if (hit) {
            openDrawer(hit.id);
            setQuery("");
        } else {
            navigate(`/list?q=${encodeURIComponent(query)}`);
        }
    };

    return (
        <div className="topbar">
            <div className="crumbs">
                <span>Acme</span>
                <span className="crumb-sep">/</span>
                <strong>{title}</strong>
            </div>

            <div className="search">
                <span style={{ color: "var(--text-dim)" }}>⌕</span>
                <input
                    id="global-search"
                    placeholder="Search tasks, codes, comments…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && submitSearch()}
                />
                <span className="search-kbd">⌘K</span>
            </div>

            <button
                className="btn btn-primary"
                onClick={() => {
                    const t = useStore.getState().createTask({ title: "New task" });
                    openDrawer(t.id);
                }}
            >
                <span>+</span> New task
            </button>
        </div>
    );
}
