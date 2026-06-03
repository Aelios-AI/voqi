import path from "path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * Build target for the embeddable AeliosSpark widget.
 *
 * Output: ``dist/aelios-spark-widget.js`` — a single IIFE with React + the
 * runtime + UI inlined, so it can be dropped on any third-party site
 * via a ``<script>`` tag without imposing peer-dependency requirements.
 *
 * The host page sees one global, ``window.AeliosSpark``. The widget renders
 * inside a Shadow DOM so host CSS can't bleed into the chrome.
 *
 * Run with: ``npm run build`` (or ``npm run dev`` to rebuild on save).
 */
export default defineConfig({
    plugins: [react()],
    define: {
        "process.env.NODE_ENV": JSON.stringify("production"),
    },
    build: {
        outDir: "dist",
        emptyOutDir: true,
        sourcemap: true,
        cssCodeSplit: false,
        lib: {
            entry: path.resolve(__dirname, "./src/index.tsx"),
            name: "AeliosSpark",
            formats: ["iife"],
            fileName: () => "aelios-spark-widget.js",
        },
        rollupOptions: {
            output: {
                inlineDynamicImports: true,
            },
        },
    },
});
