/**
 * Compact pill-form widget CSS, scoped via Shadow DOM.
 *
 * Default theme is dark pill + pure-white accent. Four CSS custom
 * properties drive every colour decision below — set them on
 * ``[data-aelios-spark-host]`` to retheme:
 *
 *   --aelios-spark-bg       pill body (default #0A0A0A)
 *   --aelios-spark-text     primary text + neutral hover ink (#F4F5F7)
 *   --aelios-spark-muted    status text, secondary glyphs (#A0A0A0)
 *   --aelios-spark-primary  accent: indicator glyph, dot pulse, send button,
 *                     user-message bubble, focus ring (#F4F5F7)
 *
 * Two visual states share one structure:
 *
 *   IDLE  — dark pill, accent indicator glyph + "Talk" label, accent
 *           glow, small status dot in the top-right corner. Clicking
 *           starts the session.
 *
 *   ACTIVE — same dark pill grows inline secondary controls (mute /
 *           type / chevron). The indicator glyph swaps by state (mic
 *           when listening, dots when connecting, exclamation on
 *           error). Pill chrome stays dark; only the status dot +
 *           indicator colour change so the visitor reads state without
 *           losing the brand chrome.
 *
 * Voice IS the UI. Transcripts surface as a transient caption above
 * the pill while the bot speaks, or a popover when the user explicitly
 * opens the chevron — never as a chat panel.
 */
export const WIDGET_CSS = `
:host {
    /* Default: dark pill + pure-white accent. The four tokens below are
       the *only* knobs the host page should override (--aelios-spark-bg /
       --aelios-spark-text / --aelios-spark-muted / --aelios-spark-primary). Everything
       else in this stylesheet derives from them via color-mix so a
       theme swap stays internally consistent. */
    --aelios-spark-primary: #F4F5F7;
    --aelios-spark-primary-soft: color-mix(in srgb, var(--aelios-spark-primary) 18%, transparent);
    --aelios-spark-bg: #0A0A0A;
    --aelios-spark-text: #F4F5F7;
    --aelios-spark-muted: #A0A0A0;
    --aelios-spark-border: color-mix(in srgb, var(--aelios-spark-primary) 40%, transparent);
    --aelios-spark-border-strong: color-mix(in srgb, var(--aelios-spark-primary) 65%, transparent);
    /* The colour that goes ON TOP of --aelios-spark-primary surfaces — the
       send button icon, the user message bubble text. Must always be
       opaque and high-contrast against the accent. The host page is
       responsible for setting this; the widget defaults to dark
       (works on the widget's default white accent). For dark accents
       (e.g. a black-primary theme) override to #F4F5F7. */
    --aelios-spark-on-primary: #0A0A0A;

    /* State accent inks — applied to the indicator + status dot only,
       so the dark pill chrome stays consistent. Minimal palette: the
       brand primary handles idle / live / working / listening — the
       icon glyph + dot pulse communicate WHICH active state we're
       in, not the colour. Red is reserved EXCLUSIVELY for connection
       issues so its appearance is unambiguous. */
    --pill-active-ink: var(--aelios-spark-primary);
    /* Error ink blends a saturated red base with the theme's text
       colour. The "red signal" stays unambiguous across every theme
       while the blend gives proper contrast against both the cream /
       mint pill bodies (where pure light-coral washed out) and the
       black / sapphire pill bodies (where dark red disappeared). */
    --pill-error-ink: color-mix(in srgb, #E11D48 65%, var(--aelios-spark-text));

    --pill-shadow: 0 6px 18px color-mix(in srgb, var(--aelios-spark-primary) 22%, transparent),
                   0 1px 2px rgba(0, 0, 0, 0.4);
    --pill-shadow-hover: 0 10px 24px color-mix(in srgb, var(--aelios-spark-primary) 30%, transparent),
                         0 2px 4px rgba(0, 0, 0, 0.5);

    all: initial;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--aelios-spark-text);
    line-height: 1.4;
    z-index: 2147483646;
}

* { box-sizing: border-box; }

.root {
    position: fixed;
    z-index: 2147483646;
    display: flex;
    flex-direction: column;
    gap: 8px;
    pointer-events: none;
}
/* Bottom-corner anchors only. Top anchors aren't supported because
   the transcript popover and caption stack expand UPWARD from the
   pill — anchoring at the top would push them off the viewport. */
.root[data-position="bottom-right"] {
    bottom: 24px;
    right: 24px;
    align-items: flex-end;
    flex-direction: column;
}
.root[data-position="bottom-left"] {
    bottom: 24px;
    left: 24px;
    align-items: flex-start;
    flex-direction: column;
}
.root > * { pointer-events: auto; }


/* ── Pill (the only thing that's visible by default) ───────────── */

.pill {
    position: relative;
    display: inline-flex;
    align-items: stretch;
    background: var(--aelios-spark-bg);
    color: var(--aelios-spark-primary);
    border: 1px solid var(--aelios-spark-border);
    border-radius: 22px;
    overflow: visible;
    box-shadow: var(--pill-shadow);
    height: 44px;
    transition: border-color 150ms ease, box-shadow 150ms ease, transform 120ms ease;
}
.pill:hover {
    border-color: var(--aelios-spark-border-strong);
    box-shadow: var(--pill-shadow-hover);
}
.pill[data-shape="circle"] { border-radius: 50%; }

/* Indicator + status-dot accent colour follows the agent state.
   We keep the palette small on purpose: idle/connecting/live/working
   all use the brand primary (only the indicator GLYPH and dot pulse
   change to communicate state); listening flashes rose; error red. */
.pill[data-state="idle"],
.pill[data-state="connecting"],
.pill[data-state="live"],
.pill[data-state="working"],
.pill[data-state="listening"]  { color: var(--pill-active-ink); }
.pill[data-state="error"]      { color: var(--pill-error-ink); }

.pill-action {
    border: 0;
    background: transparent;
    color: inherit;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 0 14px;
    height: 100%;
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.01em;
    border-radius: 22px 0 0 22px;
    transition: background-color 120ms ease, transform 120ms ease;
}
/* Hover tint is text-relative — color-mix on the text colour so a
   light theme (dark text on white pill) flips to a dark tint, and a
   dark theme (light text on black pill) keeps the white tint. */
.pill-action:not(.pill-action-static):hover  {
    background: color-mix(in srgb, var(--aelios-spark-text) 5%, transparent);
}
.pill-action:not(.pill-action-static):active { transform: translateY(1px); }
.pill-action[disabled] { opacity: 0.55; cursor: not-allowed; }
.pill-action:focus-visible {
    outline: none;
    box-shadow: inset 0 0 0 2px color-mix(in srgb, currentColor 50%, transparent);
}
/* In-session status display: NOT a button. Plain inline label that
   sits inside the pill alongside the control buttons. Uses the same
   structural rules as .pill-action so it vertically centres the same
   way — but with no background, no hover, no border-radius, no focus
   ring, so the visitor reads it as text rather than something to
   click. */
.pill-status {
    display: inline-flex;
    align-self: center;
    align-items: center;
    gap: 8px;
    padding: 0 14px 0 14px;
    height: 44px;
    line-height: 1;
    cursor: default;
    /* Inherit colour from .pill[data-state] so the indicator + label
       both pick up the brand primary (idle/live/working), rose
       (listening), or red (error) — same accent rule the launcher
       button uses. */
    color: inherit;
    user-select: none;
}
.pill-status-text {
    font-size: 13px;
    font-weight: 600;
    line-height: 1;
    color: currentColor;
    white-space: nowrap;
    letter-spacing: 0.01em;
}

/* When secondary buttons are present they sit to the right; the
   action button keeps its left-rounded radius. */
.pill .pill-action:not(:only-child) {
    padding: 0 12px 0 14px;
}
/* When the action button is the only child, round both ends. */
.pill .pill-action:only-child {
    border-radius: 22px;
    padding: 0 16px;
}

.pill-indicator {
    display: inline-flex;
    width: 16px;
    height: 16px;
    align-items: center;
    justify-content: center;
}
.pill-indicator svg { width: 16px; height: 16px; }

.pill-label {
    color: var(--aelios-spark-primary);
    white-space: nowrap;
}
.pill-label-muted {
    color: var(--aelios-spark-muted);
    font-weight: 500;
    font-size: 12px;
}

.pill-divider {
    width: 1px;
    background: color-mix(in srgb, var(--aelios-spark-primary) 22%, transparent);
    margin: 8px 0;
}

.pill-secondary {
    border: 0;
    background: transparent;
    color: var(--aelios-spark-muted);
    cursor: pointer;
    width: 36px;
    height: 100%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    transition: color 120ms ease, background-color 120ms ease;
}
.pill-secondary:hover {
    background: color-mix(in srgb, var(--aelios-spark-text) 6%, transparent);
    color: var(--aelios-spark-text);
}
.pill-secondary:active {
    background: color-mix(in srgb, var(--aelios-spark-text) 10%, transparent);
}
.pill-secondary[disabled] {
    opacity: 0.4;
    cursor: not-allowed;
}
.pill-secondary[aria-pressed="true"] {
    background: var(--aelios-spark-primary-soft);
    color: var(--aelios-spark-primary);
}

/* End-session button. Warm red on hover signals "destructive", but
   blended with the active theme via color-mix so it reads as a
   harmonised muted red on light themes (cream / mint / etc) instead
   of a bright cherry that fights the brand chrome. The red base stays
   constant; only how much of it shows through changes per theme. */
.pill-end { color: var(--aelios-spark-muted); }
.pill-end:hover {
    background: color-mix(in srgb, #E11D48 12%, transparent);
    color: color-mix(in srgb, #E11D48 65%, var(--aelios-spark-text));
}

/* ── Custom tooltips ───────────────────────────────────────────────
   Replaces the browser default title attribute (which has a ~1.5s
   show delay we can't override). Any element with a data-tooltip
   attribute gets a styled bubble that appears IMMEDIATELY on hover
   or keyboard focus. */
[data-tooltip] {
    position: relative;
}
[data-tooltip]::after {
    content: attr(data-tooltip);
    position: absolute;
    bottom: calc(100% + 8px);
    left: 50%;
    transform: translateX(-50%);
    background: color-mix(in srgb, var(--aelios-spark-bg) 96%, transparent);
    color: var(--aelios-spark-text);
    border: 1px solid color-mix(in srgb, var(--aelios-spark-text) 12%, transparent);
    padding: 5px 9px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 500;
    white-space: nowrap;
    pointer-events: none;
    opacity: 0;
    transition: opacity 80ms ease;
    z-index: 1;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
}
[data-tooltip]:hover::after,
[data-tooltip]:focus-visible::after {
    opacity: 1;
}
[data-tooltip][disabled] { cursor: not-allowed; }
[data-tooltip][disabled]:hover::after { opacity: 0; }
.pill-secondary:focus-visible {
    outline: none;
    box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--aelios-spark-primary) 60%, transparent);
}
.pill .pill-secondary:last-child {
    border-radius: 0 22px 22px 0;
}

/* ── Caption stack — IS the conversation history ────────────────────
   The stack auto-shows the latest assistant turn(s) above the pill
   when there's something new, and the chevron on the pill locks it
   open. Either way it's the same scrollable list — assistant turns
   only, oldest at top, newest at bottom. Hovering pauses the
   auto-fade timer; the visitor can scroll up through prior replies
   exactly like they're paging back through chat history. There is
   NO separate transcript popover — this stack covers both
   "transient" and "persistent" use cases with one component. */

.caption-stack {
    position: relative;
    max-width: 340px;
    width: 100%;
    /* Flex column so the up-pager / pair / down-pager stack
       vertically AND each child can use align-self to position
       itself horizontally — the pager buttons centre, the pair
       fills the full width and aligns its captions internally. */
    display: flex;
    flex-direction: column;
}
.caption-stack-pair {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    width: 100%;
}

/* Pair-paging buttons. Horizontal pill — chevron + label — centered
   above/below the pair. Click-only (wheel scroll is intentionally
   not bound, to avoid trapping the host page's scroll). Reads as a
   real button: soft elevation, hover lift, theme-aware via
   --aelios-spark-text. */
.caption-pager {
    align-self: center;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    height: 26px;
    padding: 0 14px;
    margin: 6px 0;
    background: color-mix(in srgb, var(--aelios-spark-text) 14%, var(--aelios-spark-bg));
    border: 1px solid color-mix(in srgb, var(--aelios-spark-text) 22%, transparent);
    color: var(--aelios-spark-text);
    border-radius: 999px;
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.02em;
    line-height: 1;
    cursor: pointer;
    opacity: 0.95;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.28);
    transition: opacity 120ms ease, background 120ms ease,
                border-color 120ms ease, transform 120ms ease;
    user-select: none;
}
.caption-pager:hover {
    opacity: 1;
    background: color-mix(in srgb, var(--aelios-spark-text) 22%, var(--aelios-spark-bg));
    border-color: color-mix(in srgb, var(--aelios-spark-text) 36%, transparent);
    transform: translateY(-1px);
}
.caption-pager:active { transform: translateY(0); }
.caption-pager:focus-visible {
    outline: none;
    border-color: color-mix(in srgb, var(--aelios-spark-primary) 65%, transparent);
}
.caption-pager svg { display: block; flex-shrink: 0; }
/* Real margin between the two visible captions. We don't rely on
   the flex gap property here — in some Shadow DOM contexts it
   interacts oddly with align-items. Adjacent-sibling margin is
   rock-solid. */
.caption-stack-pair > .caption + .caption {
    margin-top: 14px;
}
.root[data-position="bottom-left"] .caption-stack-pair {
    align-items: flex-start;
}

.caption {
    max-width: 320px;
    padding: 10px 14px;
    background: color-mix(in srgb, var(--aelios-spark-text) 6%, var(--aelios-spark-bg));
    color: var(--aelios-spark-text);
    border: 1px solid color-mix(in srgb, var(--aelios-spark-text) 8%, transparent);
    font-size: 13px;
    line-height: 1.45;
    border-radius: 14px;
    box-shadow: 0 12px 30px rgba(0, 0, 0, 0.32);
    animation: aelios-spark-caption-in 160ms ease-out;
    flex-shrink: 0;
}
/* Visitor's own utterance — iMessage-style. Filled bubble using the
   accent token (white in the default theme; whatever the host themed
   it to otherwise) with opaque text, right-anchored on bottom-right
   anchor (left-anchored on bottom-left). Solid + colored stands
   apart unmistakably from the agent's dark/translucent bubble. */
.caption[data-role="user"] {
    background: var(--aelios-spark-primary);
    border: 1px solid var(--aelios-spark-primary);
    color: var(--aelios-spark-on-primary);
    align-self: flex-end;
    border-bottom-right-radius: 4px;
    /* Tiny "You" tag above the bubble — relative-positioned so the
       caption itself can host the label as a ::before. */
    position: relative;
    margin-top: 14px;
}
.caption[data-role="user"]::before {
    content: "You";
    position: absolute;
    top: -14px;
    right: 6px;
    font-size: 9.5px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--aelios-spark-muted);
}
.root[data-position="bottom-left"] .caption[data-role="user"] {
    align-self: flex-start;
    border-bottom-right-radius: 14px;
    border-bottom-left-radius: 4px;
}
.root[data-position="bottom-left"] .caption[data-role="user"]::before {
    right: auto;
    left: 6px;
}

/* Agent reply — keep the existing dark/translucent treatment but
   add a matching role tag so the side-by-side stack reads as a
   real chat. */
.caption[data-role="assistant"] {
    border-bottom-left-radius: 4px;
    position: relative;
    margin-top: 14px;
}
.caption[data-role="assistant"]::before {
    content: "Agent";
    position: absolute;
    top: -14px;
    left: 6px;
    font-size: 9.5px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--aelios-spark-muted);
}
.root[data-position="bottom-left"] .caption[data-role="assistant"] {
    border-bottom-left-radius: 14px;
    border-bottom-right-radius: 4px;
}
.root[data-position="bottom-left"] .caption[data-role="assistant"]::before {
    left: auto;
    right: 6px;
}
.caption[data-recency="prior"] {
    opacity: 0.55;
    font-size: 12.5px;
}

@keyframes aelios-spark-caption-in {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: translateY(0); }
}
.caption[data-recency="prior"] {
    animation-name: aelios-spark-caption-in-faded;
}
@keyframes aelios-spark-caption-in-faded {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 0.55; transform: translateY(0); }
}

/* ── Hint banner (session-ending, expiration heads-up) ──────────
   Single neutral style: dark glassy background, subtle grey border,
   light text. NO yellow / amber / orange — every soft notice uses the
   same calm chrome. The pill is the primary signal; this is just a
   short ambient note that auto-clears in ~10s. */

.toast {
    max-width: 300px;
    padding: 8px 12px;
    /* Themed glass: slight darken of the theme bg with theme text.
       Matches the assistant caption treatment so the toast reads as
       "part of the same surface" on every theme — cream toast on
       cream pill, midnight toast on midnight pill, etc. */
    background: color-mix(in srgb, var(--aelios-spark-text) 6%, var(--aelios-spark-bg));
    color: var(--aelios-spark-text);
    border: 1px solid color-mix(in srgb, var(--aelios-spark-text) 12%, transparent);
    border-radius: 10px;
    font-size: 12.5px;
    line-height: 1.45;
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.35);
    display: flex;
    align-items: flex-start;
    gap: 8px;
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
}
.toast strong { font-weight: 600; color: var(--aelios-spark-text); }
.toast-warn,
.toast-error {
    /* Both legacy variants collapse onto the neutral style — kept as
       selectors so existing markup doesn't need a sweep. */
}
.toast-dismiss {
    margin-left: auto;
    background: transparent;
    border: 0;
    color: var(--aelios-spark-muted);
    font-size: 16px;
    line-height: 1;
    cursor: pointer;
    padding: 0 4px;
    transition: color 120ms ease;
}
.toast-dismiss:hover { color: var(--aelios-spark-text); }

/* ── Text input row ────────────────────────────────────────────── */

.text-input-row {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--aelios-spark-bg);
    border: 1px solid var(--aelios-spark-border);
    border-radius: 22px;
    padding: 4px 4px 4px 14px;
    box-shadow: var(--pill-shadow);
    width: 320px;
    height: 44px;
}
.text-input {
    flex: 1;
    border: 0;
    outline: none;
    background: transparent;
    font: inherit;
    font-size: 14px;
    color: var(--aelios-spark-text);
    padding: 0;
}
.text-input::placeholder { color: var(--aelios-spark-muted); }
.text-send {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    border: 0;
    background: var(--aelios-spark-primary);
    /* Always reach for --aelios-spark-on-primary, NOT --aelios-spark-bg — the bg
       can be translucent in glass themes, which would make the icon
       disappear when primary and bg both happen to be white. */
    color: var(--aelios-spark-on-primary);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: background-color 120ms ease, opacity 120ms ease;
}
.text-send:hover {
    background: color-mix(in srgb, var(--aelios-spark-primary) 80%, var(--aelios-spark-bg));
}
.text-send[disabled] {
    opacity: 0.4;
    cursor: not-allowed;
}

/* ── Minimize / restore ─────────────────────────────────────────────
   The minimize button is a thin column at the right end of the pill —
   narrower than a regular .pill-secondary so it reads as a passive
   "tuck away" handle rather than a primary control. It's always the
   DOM-last child of .pill, so the existing
   '.pill .pill-secondary:last-child' rule no longer matches the End
   button (the previous visual edge); '.pill-minimize' carries the
   right-edge rounding now. When clicked, .root[data-minimized] slides
   everything off-screen horizontally, leaving only .restore-tab
   pinned to the screen edge. */

.pill-minimize {
    border: 0;
    background: transparent;
    color: color-mix(in srgb, var(--aelios-spark-muted) 70%, transparent);
    cursor: pointer;
    width: 22px;
    height: 100%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    border-radius: 0 22px 22px 0;
    transition: color 120ms ease, background-color 120ms ease;
}
.pill-minimize:hover {
    color: var(--aelios-spark-text);
    background: color-mix(in srgb, var(--aelios-spark-text) 5%, transparent);
}
.pill-minimize:focus-visible {
    outline: none;
    box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--aelios-spark-primary) 50%, transparent);
}

/* Slide the entire widget shell DOWN past the bottom of the viewport.
   100% is the .root's own height; +48px clears the .root's own bottom
   offset (24px) plus a margin so the shadow doesn't peek up from the
   bottom edge. The transition runs both ways so restoring is the same
   motion in reverse. aria-hidden + pointer-events:none below pull the
   off-screen DOM out of focus and click reach. */
.root[data-minimized="true"] {
    transform: translateY(calc(100% + 48px));
    transition: transform 280ms cubic-bezier(0.4, 0, 0.2, 1);
    pointer-events: none;
}
/* The general '.root > *' rule re-enables pointer-events on every
   child; we have to explicitly squash them back off when minimized so
   off-screen controls aren't still clickable through the screen edge. */
.root[data-minimized="true"] > * {
    pointer-events: none;
}
.root {
    transition: transform 280ms cubic-bezier(0.4, 0, 0.2, 1);
}

/* Restore tab — horizontal pill at the bottom of the viewport in the
   same corner the widget anchors to. With the widget tucked DOWN, the
   bottom edge of the screen is now the natural home for the affordance
   that brings it back UP. Bottom-anchoring also gives us horizontal
   room for an actual text label, so the gesture is unambiguous (a
   chevron sliver on a side edge had no chance against random page
   chrome). The bottom edge is square (flush with the viewport); the
   top is pill-rounded so it reads as a tab popping up from the floor. */

.restore-tab {
    position: fixed;
    bottom: 0;
    z-index: 2147483646;
    height: 28px;
    padding: 0 11px 0 9px;
    background: color-mix(in srgb, var(--aelios-spark-primary) 14%, var(--aelios-spark-bg));
    color: var(--aelios-spark-primary);
    border: 1px solid color-mix(in srgb, var(--aelios-spark-primary) 40%, transparent);
    border-bottom: 0;
    border-radius: 10px 10px 0 0;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font: inherit;
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.01em;
    line-height: 1;
    box-shadow:
        0 -6px 20px color-mix(in srgb, var(--aelios-spark-primary) 24%, transparent),
        0 0 0 1px color-mix(in srgb, var(--aelios-spark-primary) 10%, transparent);
    transition: color 120ms ease, background-color 120ms ease,
                border-color 120ms ease, transform 140ms ease,
                box-shadow 140ms ease;
    animation: aelios-spark-restore-in 240ms cubic-bezier(0.4, 0, 0.2, 1);
}
.restore-tab[data-position="bottom-right"] { right: 24px; }
.restore-tab[data-position="bottom-left"]  { left: 24px; }
.restore-tab:hover {
    transform: translateY(-2px);
    background: color-mix(in srgb, var(--aelios-spark-primary) 22%, var(--aelios-spark-bg));
    border-color: color-mix(in srgb, var(--aelios-spark-primary) 65%, transparent);
    box-shadow:
        0 -10px 28px color-mix(in srgb, var(--aelios-spark-primary) 36%, transparent),
        0 0 0 1px color-mix(in srgb, var(--aelios-spark-primary) 18%, transparent);
}
.restore-tab:active { transform: translateY(0); }
.restore-tab:focus-visible {
    outline: none;
    border-color: var(--aelios-spark-primary);
    box-shadow:
        0 0 0 3px color-mix(in srgb, var(--aelios-spark-primary) 30%, transparent),
        0 -6px 20px color-mix(in srgb, var(--aelios-spark-primary) 24%, transparent);
}
.restore-tab svg {
    width: 11px;
    height: 11px;
    flex-shrink: 0;
    animation: aelios-spark-restore-pulse-up 2.4s ease-in-out infinite;
}
.restore-tab:hover svg { animation: none; }
.restore-tab-label {
    color: currentColor;
    white-space: nowrap;
}
@keyframes aelios-spark-restore-pulse-up {
    0%, 100% { transform: translateY(0); opacity: 0.85; }
    50%      { transform: translateY(-2px); opacity: 1; }
}
@keyframes aelios-spark-restore-in {
    from { opacity: 0; transform: translateY(12px); }
    to   { opacity: 1; transform: translateY(0); }
}

/* ── Pre-session picker modal ───────────────────────────────────────
   Shown when the visitor clicks Talk and either (a) the agent has
   more than one configured voice language, or (b) the host registered
   tools so 'act for me' vs 'guide me' is a real choice. Two-step:
   language → mode. Either step is skipped silently when only one
   choice is valid. Backdrop click + Escape cancel without starting.
   Lives in the Shadow DOM so host CSS / z-index can't interfere. */

.picker-backdrop {
    position: fixed;
    inset: 0;
    z-index: 2147483647;
    background: rgba(10, 10, 10, 0.55);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    pointer-events: auto;
    animation: aelios-spark-picker-fade-in 160ms ease-out;
}

.picker-card {
    position: relative;
    background: var(--aelios-spark-bg);
    color: var(--aelios-spark-text);
    border: 1px solid color-mix(in srgb, var(--aelios-spark-primary) 22%, transparent);
    border-radius: 18px;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.5),
                0 0 0 1px color-mix(in srgb, var(--aelios-spark-primary) 8%, transparent);
    width: min(440px, 100%);
    max-height: calc(100vh - 40px);
    overflow-y: auto;
    padding: 24px 22px 22px 22px;
    animation: aelios-spark-picker-card-in 200ms cubic-bezier(0.4, 0, 0.2, 1);
}

.picker-close {
    position: absolute;
    top: 10px;
    right: 10px;
    width: 28px;
    height: 28px;
    border: 0;
    background: transparent;
    color: var(--aelios-spark-muted);
    cursor: pointer;
    border-radius: 8px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: color 120ms ease, background 120ms ease;
}
.picker-close:hover {
    color: var(--aelios-spark-text);
    background: color-mix(in srgb, var(--aelios-spark-text) 6%, transparent);
}
.picker-close:focus-visible {
    outline: none;
    box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--aelios-spark-primary) 50%, transparent);
}

.picker-title {
    margin: 0 0 6px 0;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.005em;
    color: var(--aelios-spark-text);
}
.picker-subtitle {
    margin: 0 0 16px 0;
    font-size: 13px;
    line-height: 1.45;
    color: var(--aelios-spark-muted);
}

/* Header row — title on the left, compact language pill on the right. */
.picker-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
    padding-right: 24px; /* room for the close button */
}
.picker-header .picker-title { margin: 0; }
/* Language pill — uses the widget's own theme tokens (--aelios-spark-bg /
   --aelios-spark-text / --aelios-spark-primary) so the picker re-themes
   automatically when the host page customises the widget. The dropdown
   menu lays all 37 languages out in a 3-column grid: every option is
   visible at once, no scrollbar, no overflow clipping. */
.picker-language {
    position: relative;
    flex-shrink: 0;
}
.picker-language-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    background: color-mix(in srgb, var(--aelios-spark-text) 6%, var(--aelios-spark-bg));
    border: 1px solid var(--aelios-spark-border);
    border-radius: 999px;
    color: color-mix(in srgb, var(--aelios-spark-text) 75%, transparent);
    cursor: pointer;
    font: inherit;
    line-height: 1;
    transition: background 180ms ease, color 180ms ease, border-color 180ms ease;
}
.picker-language-pill:hover {
    background: color-mix(in srgb, var(--aelios-spark-text) 12%, var(--aelios-spark-bg));
    color: var(--aelios-spark-text);
    border-color: var(--aelios-spark-border-strong);
}
.picker-language-pill:hover .picker-language-globe {
    transform: rotate(12deg);
}
.picker-language-pill:focus-visible {
    outline: none;
    border-color: var(--aelios-spark-primary);
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--aelios-spark-primary) 35%, transparent);
}
.picker-language-globe {
    width: 14px;
    height: 14px;
    color: var(--aelios-spark-primary);
    flex-shrink: 0;
    transition: transform 200ms ease;
}
.picker-language-code {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.picker-language-chevron {
    width: 11px;
    height: 11px;
    opacity: 0.45;
    transition: transform 200ms ease;
}
.picker-language-chevron[data-open="true"] { transform: rotate(180deg); }

/* Menu — search input on top, scrollable 3-column grid below capped at
   ~60vh so the modal never overflows. Anchored to the right edge of
   the pill, allowed to grow leftward so the wider grid clears the
   modal's right padding. */
.picker-language-menu {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    padding: 6px;
    width: max-content;
    max-width: min(360px, calc(100vw - 60px));
    background: color-mix(in srgb, var(--aelios-spark-text) 6%, var(--aelios-spark-bg));
    border: 1px solid var(--aelios-spark-border);
    border-radius: 12px;
    box-shadow: 0 18px 40px rgba(0, 0, 0, 0.45);
    z-index: 2;
    display: flex;
    flex-direction: column;
    gap: 6px;
}
.picker-language-search {
    width: 100%;
    box-sizing: border-box;
    padding: 6px 9px;
    border: 1px solid var(--aelios-spark-border);
    border-radius: 6px;
    background: color-mix(in srgb, var(--aelios-spark-text) 4%, var(--aelios-spark-bg));
    color: var(--aelios-spark-text);
    font: inherit;
    font-size: 12px;
    outline: none;
    transition: border-color 120ms ease, box-shadow 120ms ease;
}
.picker-language-search::placeholder {
    color: color-mix(in srgb, var(--aelios-spark-text) 45%, transparent);
}
.picker-language-search:focus {
    border-color: color-mix(in srgb, var(--aelios-spark-primary) 50%, transparent);
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--aelios-spark-primary) 18%, transparent);
}
.picker-language-list {
    margin: 0;
    padding: 0;
    list-style: none;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 2px;
    /* Cap at exactly 8 rows of options (each ~24px + 2px gap). Beyond
       that, scroll. Keeps the floating popover from overflowing the
       modal card's body area on small viewports. */
    max-height: calc(8 * 24px + 7 * 2px);
    overflow-y: auto;
    /* Hide scrollbar visually but keep functionality. */
    scrollbar-width: thin;
    scrollbar-color: color-mix(in srgb, var(--aelios-spark-text) 20%, transparent) transparent;
}
.picker-language-list::-webkit-scrollbar { width: 6px; }
.picker-language-list::-webkit-scrollbar-thumb {
    background: color-mix(in srgb, var(--aelios-spark-text) 20%, transparent);
    border-radius: 3px;
}
.picker-language-list li { margin: 0; padding: 0; }
.picker-language-empty {
    padding: 12px 8px;
    text-align: center;
    color: color-mix(in srgb, var(--aelios-spark-text) 50%, transparent);
    font-size: 11.5px;
}
.picker-language-option {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 5px 8px;
    border: 0;
    background: transparent;
    color: color-mix(in srgb, var(--aelios-spark-text) 80%, transparent);
    cursor: pointer;
    font: inherit;
    text-align: left;
    border-radius: 6px;
    transition: background 120ms ease, color 120ms ease;
}
.picker-language-option:hover {
    background: color-mix(in srgb, var(--aelios-spark-text) 10%, transparent);
    color: var(--aelios-spark-text);
}
.picker-language-option[data-active="true"] {
    background: color-mix(in srgb, var(--aelios-spark-primary) 16%, transparent);
    color: var(--aelios-spark-primary);
}
.picker-language-option:focus-visible {
    outline: none;
    box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--aelios-spark-primary) 60%, transparent);
}
.picker-language-option-flag { font-size: 13px; line-height: 1; flex-shrink: 0; }
.picker-language-option-name {
    font-size: 11.5px;
    font-weight: 500;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* Defocus the body + footer when the language dropdown is open:
   light blur, half opacity, pointer-events suppressed so a stray
   click can't fire a mode card while the dropdown is the focus. The
   header stays sharp so the dropdown reads cleanly above. */
.picker-defocusable {
    transition: filter 200ms ease, opacity 200ms ease;
}
.picker-defocusable[data-defocused="true"] {
    filter: blur(2px);
    opacity: 0.45;
    pointer-events: none;
}

/* Single Start button used when there's no mode picker (no host
   tools registered, so guide is the only valid mode and the modal's
   only real choice was the language). */
.picker-start {
    width: 100%;
    margin-top: 10px;
    padding: 12px 16px;
    border: 0;
    border-radius: 12px;
    background: var(--aelios-spark-primary);
    color: var(--aelios-spark-on-primary);
    font: inherit;
    font-size: 13.5px;
    font-weight: 700;
    letter-spacing: 0.01em;
    cursor: pointer;
    transition: background 120ms ease, transform 120ms ease;
}
.picker-start:hover {
    background: color-mix(in srgb, var(--aelios-spark-primary) 88%, var(--aelios-spark-bg));
    transform: translateY(-1px);
}
.picker-start:active { transform: translateY(0); }
.picker-start:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--aelios-spark-primary) 38%, transparent);
}

.picker-options-stack {
    display: flex;
    flex-direction: column;
    gap: 10px;
}
.picker-option-card {
    border: 1px solid color-mix(in srgb, var(--aelios-spark-text) 12%, transparent);
    background: color-mix(in srgb, var(--aelios-spark-text) 4%, var(--aelios-spark-bg));
    color: var(--aelios-spark-text);
    cursor: pointer;
    border-radius: 12px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    text-align: left;
    font: inherit;
    transition: border-color 120ms ease, background 120ms ease,
                transform 120ms ease;
}
.picker-option-card:hover {
    border-color: color-mix(in srgb, var(--aelios-spark-primary) 55%, transparent);
    background: color-mix(in srgb, var(--aelios-spark-text) 8%, var(--aelios-spark-bg));
    transform: translateY(-1px);
}
.picker-option-card:active { transform: translateY(0); }
.picker-option-card:focus-visible {
    outline: none;
    border-color: var(--aelios-spark-primary);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--aelios-spark-primary) 28%, transparent);
}
.picker-option-card-title {
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.005em;
    color: var(--aelios-spark-text);
}
.picker-option-card-body {
    font-size: 12.5px;
    line-height: 1.5;
    color: var(--aelios-spark-muted);
}

@keyframes aelios-spark-picker-fade-in {
    from { opacity: 0; }
    to   { opacity: 1; }
}
@keyframes aelios-spark-picker-card-in {
    from { opacity: 0; transform: translateY(12px) scale(0.98); }
    to   { opacity: 1; transform: translateY(0) scale(1); }
}

/* ── Guide-mode ghost cursor ────────────────────────────────────────
   Real mouse-pointer SVG at the LLM-picked coordinate, with a small
   "Agent" pill anchored just below the pointer's tail so the visitor
   reads it as "this is the agent's cursor, not yours". The cursor's
   tip lands exactly on the (left, top) coordinate so the pointer
   actually points AT the target. Always non-interactive — visitor
   must still click the real UI element. Auto-fades after 20s,
   replaced by the next guide_cursor message before that. */

.guide-cursor {
    position: fixed;
    z-index: 2147483646;
    /* The pointer's tip is the top-left of the SVG (path starts at
       2,2). We DON'T centre the wrapper — the inline left/top is
       where the tip should land, so we leave them unshifted and the
       SVG's natural origin lines up with the coord. */
    pointer-events: none;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    animation: aelios-spark-cursor-in 220ms cubic-bezier(0.4, 0, 0.2, 1);
}

.guide-cursor-pointer {
    display: block;
    /* A subtle drop-shadow so the cursor reads cleanly on both light
       and dark host pages — without it the black outline can blend
       into dark backgrounds. The blue glow pulses gently to convey
       liveness without moving the pointer (any positional bob would
       offset the cursor's tip from the bot's picked coord, which is
       the whole point of the cursor). */
    filter:
        drop-shadow(0 2px 4px rgba(0, 0, 0, 0.35))
        drop-shadow(0 0 6px rgba(59, 130, 246, 0.45));
    animation: aelios-spark-cursor-glow 1.6s ease-in-out infinite;
}

.guide-cursor-tag {
    /* Sits below the pointer's tail (~26px down from the tip).
       Indented 6px so the pill aligns under the mid of the cursor
       body rather than the tip. */
    margin-left: 6px;
    display: inline-flex;
    align-items: center;
    padding: 3px 9px;
    background: rgba(10, 10, 10, 0.94);
    color: #F4F5F7;
    border: 1px solid rgba(59, 130, 246, 0.5);
    border-radius: 999px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.02em;
    line-height: 1;
    box-shadow:
        0 4px 10px rgba(0, 0, 0, 0.35),
        0 0 0 1px rgba(59, 130, 246, 0.18);
    white-space: nowrap;
}
.guide-cursor-tag-text { color: #F4F5F7; }

@keyframes aelios-spark-cursor-glow {
    0%, 100% {
        filter:
            drop-shadow(0 2px 4px rgba(0, 0, 0, 0.35))
            drop-shadow(0 0 6px rgba(59, 130, 246, 0.45));
    }
    50% {
        filter:
            drop-shadow(0 2px 4px rgba(0, 0, 0, 0.35))
            drop-shadow(0 0 12px rgba(59, 130, 246, 0.7));
    }
}
@keyframes aelios-spark-cursor-in {
    from { opacity: 0; transform: scale(0.7); transform-origin: top left; }
    to   { opacity: 1; transform: scale(1); transform-origin: top left; }
}

`;
