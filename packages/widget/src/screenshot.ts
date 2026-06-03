/**
 * The widget snapshots the host page on demand from the agent
 * (over the RTVI ``request_screenshot`` server message). This module
 * owns the browser-side capture: rasterise the DOM via html2canvas,
 * crop to a sane upper bound, encode JPEG, and return the base64.
 *
 * Notes on what gets captured:
 *
 * - We snapshot ``document.body``, NOT the full document. That avoids
 *   the html2canvas-pro quirks around html/body overflow and gives a
 *   pixel rect that lines up with what the visitor sees in the tab.
 *
 * - The widget's own host element (``[data-aelios-spark-host]``) is excluded
 *   via the ``ignoreElements`` callback so the agent doesn't see its
 *   own pill / transcript popover and start commenting on them.
 *
 * - ``foreignObjectRendering: false`` — html2canvas's foreignObject
 *   path is faster but breaks on cross-origin fonts and produces
 *   blank output when CSP refuses ``data:`` URIs in <img> tags.
 *   Silent failure is the worst kind, so we accept the slower
 *   per-element render.
 *
 * - Cross-origin iframes will appear blank in the snapshot (they're
 *   invisible to the parent JS context). This is a limitation of the
 *   approach, not a bug — see the architecture discussion in
 *   ``screenshot_service.py``.
 */

export interface CaptureSuccess {
    ok: true;
    image_b64: string;        // raw base64, no "data:" prefix
    mime: string;             // "image/jpeg"
    width: number;
    height: number;
}

export interface CaptureFailure {
    ok: false;
    error: string;
}

export type CaptureResult = CaptureSuccess | CaptureFailure;

export interface CaptureOptions {
    /** Hard cap on the longest dimension of the produced image. The
     *  source DOM is rendered at devicePixelRatio, then downscaled.
     *  Larger = more useful for the LLM, but more bytes on the wire
     *  and more inference latency. 1280 is a comfortable middle. */
    maxDimension?: number;
    /** JPEG quality 0..1. Lower = smaller payload + lossier. 0.7
     *  keeps text legible while shaving ~60% off PNG size. */
    jpegQuality?: number;
    /** Per-capture wall-clock budget. If html2canvas hasn't returned
     *  by this point we resolve with a failure — the server side has
     *  its own 2s timeout but we want to free the browser as well. */
    timeoutMs?: number;
}

const DEFAULT_OPTIONS: Required<CaptureOptions> = {
    maxDimension: 1280,
    jpegQuality: 0.7,
    timeoutMs: 1800,
};

/**
 * Capture a screenshot of the host page DOM. Returns a base64 JPEG
 * (no data: prefix) plus dimensions on success, or a failure with a
 * human-readable error string. Never throws.
 */
export async function captureHostScreenshot(
    options: CaptureOptions = {},
): Promise<CaptureResult> {
    const opts = { ...DEFAULT_OPTIONS, ...options };

    if (typeof document === "undefined" || !document.body) {
        return { ok: false, error: "no document.body available" };
    }

    // Race the actual render against a soft timeout so the caller
    // never blocks indefinitely if html2canvas trips on something.
    const renderPromise = renderToCanvas(opts);
    const timeoutPromise = new Promise<CaptureResult>((resolve) =>
        window.setTimeout(
            () => resolve({ ok: false, error: `capture timed out after ${opts.timeoutMs}ms` }),
            opts.timeoutMs,
        ),
    );

    try {
        return await Promise.race([renderPromise, timeoutPromise]);
    } catch (err) {
        return {
            ok: false,
            error: err instanceof Error ? err.message : String(err),
        };
    }
}

async function renderToCanvas(
    opts: Required<CaptureOptions>,
): Promise<CaptureResult> {
    // Lazy-import html2canvas-pro so the rest of the widget bundle
    // doesn't pay its 770KB at module load (and so any runtime
    // initialization quirk in the library can't break widget mount —
    // the worst it can do is fail this one capture).
    let html2canvas: (
        el: HTMLElement,
        opts: Record<string, unknown>,
    ) => Promise<HTMLCanvasElement>;
    try {
        const mod = await import("html2canvas-pro");
        html2canvas = (mod as { default: typeof html2canvas }).default;
    } catch (err) {
        return {
            ok: false,
            error: `html2canvas-pro import failed: ${err instanceof Error ? err.message : String(err)}`,
        };
    }

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let raw: HTMLCanvasElement;
    try {
        raw = await html2canvas(document.body, {
            backgroundColor: null,
            useCORS: true,
            logging: false,
            scale: dpr,
            // foreignObject path: the browser rasterises live DOM
            // wrapped in <foreignObject>, so cross-origin CSS still
            // applies (the browser doesn't need cssRules access the
            // way the per-element fallback does). Trade-off: cross-
            // origin web fonts fall back to defaults inside the SVG,
            // and host CSPs that forbid data: in <img> can break it.
            // Both are independent of the host site cooperating.
            foreignObjectRendering: true,
            ignoreElements: (el: Element) => {
                if (el instanceof HTMLElement) {
                    if (el.hasAttribute("data-aelios-spark-host")) return true;
                    if (el.closest?.("[data-aelios-spark-host]")) return true;
                }
                return false;
            },
        });
    } catch (err) {
        return {
            ok: false,
            error: `html2canvas failed: ${err instanceof Error ? err.message : String(err)}`,
        };
    }

    if (raw.width === 0 || raw.height === 0) {
        return { ok: false, error: "captured canvas was empty" };
    }

    // Downscale so the longest side is at most maxDimension. Saves bytes
    // and inference cost on huge displays.
    const longest = Math.max(raw.width, raw.height);
    const scale = longest > opts.maxDimension ? opts.maxDimension / longest : 1;
    const targetW = Math.max(1, Math.round(raw.width * scale));
    const targetH = Math.max(1, Math.round(raw.height * scale));

    let finalCanvas: HTMLCanvasElement;
    if (scale === 1) {
        finalCanvas = raw;
    } else {
        finalCanvas = document.createElement("canvas");
        finalCanvas.width = targetW;
        finalCanvas.height = targetH;
        const ctx = finalCanvas.getContext("2d");
        if (!ctx) {
            return { ok: false, error: "could not get 2d context for downscale" };
        }
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(raw, 0, 0, targetW, targetH);
    }

    const dataUrl = finalCanvas.toDataURL("image/jpeg", opts.jpegQuality);
    const commaAt = dataUrl.indexOf(",");
    if (commaAt === -1) {
        return { ok: false, error: "toDataURL returned malformed string" };
    }

    return {
        ok: true,
        image_b64: dataUrl.slice(commaAt + 1),
        mime: "image/jpeg",
        width: targetW,
        height: targetH,
    };
}
