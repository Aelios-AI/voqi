import { useEffect } from "react";
import { BrowserRouter, Route, Routes, useNavigate } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { Topbar } from "./components/Topbar";
import { TaskDrawer } from "./components/TaskDrawer";
import { Toasts } from "./components/Toasts";
import { BulkBar } from "./components/BulkBar";
import { Dashboard } from "./pages/Dashboard";
import { Board } from "./pages/Board";
import { List } from "./pages/List";
import { Sprints } from "./pages/Sprints";
import { Team } from "./pages/Team";
import { Inbox } from "./pages/Inbox";
import { Settings } from "./pages/Settings";
import { installExampleApi } from "./api/exampleActions";

// Exposes react-router's `navigate` to the Voqi adapter, which runs
// outside React (in voqi.ts) and therefore cannot call useNavigate()
// directly. The navigate_to_page tool reads off this global.
function NavBridge() {
    const navigate = useNavigate();
    useEffect(() => {
        window.__trackerNavigate = (path: string) => navigate(path);
        return () => {
            delete window.__trackerNavigate;
        };
    }, [navigate]);
    return null;
}

function Shell() {
    useEffect(() => {
        installExampleApi();
    }, []);

    return (
        <div className="app">
            <Sidebar />
            <main className="main">
                <Topbar />
                <div className="content">
                    <Routes>
                        <Route path="/" element={<Dashboard />} />
                        <Route path="/board" element={<Board />} />
                        <Route path="/list" element={<List />} />
                        <Route path="/sprints" element={<Sprints />} />
                        <Route path="/team" element={<Team />} />
                        <Route path="/inbox" element={<Inbox />} />
                        <Route path="/settings" element={<Settings />} />
                    </Routes>
                </div>
            </main>
            <TaskDrawer />
            <BulkBar />
            <Toasts />
        </div>
    );
}

export default function App() {
    return (
        <BrowserRouter>
            <NavBridge />
            <Shell />
        </BrowserRouter>
    );
}
