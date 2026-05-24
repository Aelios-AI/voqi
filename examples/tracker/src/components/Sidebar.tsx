import { NavLink } from "react-router-dom";
import { useStore } from "../store";
import { Avatar } from "./Avatar";

const NAV = [
    { to: "/", label: "Dashboard", icon: "◇", end: true },
    { to: "/board", label: "Board", icon: "▦" },
    { to: "/list", label: "List", icon: "≡" },
    { to: "/inbox", label: "Inbox", icon: "✉" },
];

const PLAN = [
    { to: "/sprints", label: "Sprints", icon: "▷" },
    { to: "/team", label: "Team", icon: "○" },
    { to: "/settings", label: "Settings", icon: "⚙" },
];

export function Sidebar() {
    const me = useStore((s) => s.members.find((m) => m.id === s.currentUserId));
    return (
        <aside className="sidebar">
            <div className="brand">
                <div className="brand-mark" />
                <div>
                    <div className="brand-name">EXAMPLE TRACKER</div>
                    <div className="brand-sub">Acme Workspace</div>
                </div>
            </div>

            <div className="nav-section">Workspace</div>
            {NAV.map((item) => (
                <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
                >
                    <span className="nav-icon">{item.icon}</span>
                    {item.label}
                </NavLink>
            ))}

            <div className="nav-section">Planning</div>
            {PLAN.map((item) => (
                <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
                >
                    <span className="nav-icon">{item.icon}</span>
                    {item.label}
                </NavLink>
            ))}

            <div className="nav-spacer" />

            <div className="user-pill">
                <Avatar member={me} />
                <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="user-pill-name" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {me?.name ?? "Unknown"}
                    </div>
                    <div className="user-pill-role">{me?.role ?? ""}</div>
                </div>
            </div>
        </aside>
    );
}
