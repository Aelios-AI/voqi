# Roadmap

Where Aelios Spark is going next. Roughly ordered by impact, not by date — we
ship when a piece is ready, not on a calendar.

- **Mobile UI.** Responsive widget chrome for small viewports plus a
  voice loop that makes sense on a phone (no on-screen pointing, likely
  a push-to-talk mic instead of continuous VAD). Today the widget
  explicitly refuses to mount below 768px.

- **Easy provider swaps.** STT / TTS / LLM choice driven from
  `aelios-spark.config.yaml` instead of editing `bot.py` and the two
  LangChain call sites. One config block per session, hot-swappable
  across runs.

- **Push-to-talk mode.** Hold-to-speak as an alternative to continuous
  VAD. Useful in noisy rooms, on tablets, and for users who want
  explicit control over when the mic is hot.

- **Wake-word ("Hey Aelios Spark").** Optional always-on listener so the
  launcher click stops being mandatory. Picovoice Porcupine or similar,
  fully on-device.

- **MCP support.** Let the agent consume tools from any
  [Model Context Protocol](https://modelcontextprotocol.io) server in
  addition to JS-defined tools. Drops the entire MCP ecosystem into
  Aelios Spark without the host page having to write tool defs by hand.

- **Sub-agents.** One main agent that can hand a turn off to a
  specialist (billing, support, search, anything you wire up). The
  main agent owns conversation continuity; the sub-agent runs a
  focused loop and returns control when it's done.

- **Memory.** An optional persistence layer for facts you want
  available to the agent across sessions (preferences, prior context,
  custom user data). Off by default; storage backend pluggable.

- **More example software.** Drop-in example apps wired to the widget
  across stacks (Next, Vue, Svelte, plain HTML) and verticals (CRM,
  kanban, settings panel, knowledge base). Goal: any developer can
  copy an example that matches their app shape and have voice control
  working in minutes.

---

Want one of these sooner, or have an idea that isn't here? Open a
discussion or a PR.
