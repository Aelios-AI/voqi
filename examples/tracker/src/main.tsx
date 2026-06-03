import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./aelios-spark";
import { embedAeliosSparkWidget } from "./embedAeliosSparkWidget";

embedAeliosSparkWidget();

ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
        <App />
    </React.StrictMode>,
);
