import { useEffect } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
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
            <Shell />
        </BrowserRouter>
    );
}
