/// <reference types="vite/client" />
/**
 * Inject the AeliosSpark widget <script> tag at runtime. The widget exposes a
 * ``window.AeliosSparkReady`` queue, so order-vs-registration doesn't matter
 * — tool handlers in aelios-spark.ts push into the queue and run whenever the
 * bundle finishes loading.
 *
 * The widget bundle is built by the sibling ``packages/widget`` package
 * and served from its dist folder. For local dev, point AGENT_URL at
 * the running agent server (see ``packages/agent-server``).
 */

const WIDGET_BUNDLE = "/aelios-spark-widget.js";
const AGENT_URL = "http://localhost:3002/start";

// Auth between widget and agent server is purely origin-based — the
// agent server reads ``AELIOS_SPARK_ALLOWED_ORIGINS`` and the browser refuses
// to talk to it from anywhere else. No tokens to coordinate here.

export function embedAeliosSparkWidget(): void {
    const script = document.createElement("script");
    script.src = WIDGET_BUNDLE;
    script.async = true;
    script.dataset.agentUrl = AGENT_URL;
    document.head.appendChild(script);
}
