# Writing tools

Tools are the functions your voice agent can call. The widget keeps a
registry of them in the user's browser; the agent decides which to
invoke based on what the user said. You write tools the same way you'd
write a small, well-documented function — with a critical extra: the
`description` is the prompt the LLM reads when deciding whether to
call.

## Anatomy

```ts
Voqi.defineTool({
    name: "create_task",
    description:
        "Create a new task. Use when the user says 'create', 'add', " +
        "or describes a new item they want to track. If the user " +
        "doesn't give a priority, default to 'medium'.",
    parameters: {
        type: "object",
        properties: {
            title: { type: "string", description: "Short headline. Required." },
            priority: {
                type: "string",
                enum: ["urgent", "high", "medium", "low"],
                description: "Defaults to 'medium' if unspecified.",
            },
            assignee: {
                type: "string",
                description: "Member name or id. Optional.",
            },
        },
        required: ["title"],
    },
    execute: async ({ title, priority, assignee }) => {
        const id = await myApi.createTask({ title, priority, assignee });
        return { id, title, status: "created" };
    },
    requiresConfirmation: false,
});
```

## Fields

| Field | Notes |
|---|---|
| `name` | Stable identifier. The agent uses it verbatim. Snake-case is conventional. |
| `description` | Read by the LLM **every turn**. Write like a prompt: tell it *when* to call (triggers, synonyms) and what it does. Vague descriptions = vague calls. |
| `parameters` | JSON Schema. Use `enum` liberally — enums dramatically improve the agent's argument formatting. |
| `execute` | Your function. Args come in matching the schema. Whatever you return becomes part of the agent's next prompt — so return rich data, not just `{ ok: true }`. Throw to surface the error to the agent. |
| `requiresConfirmation` | When true, the agent verbally asks the visitor to confirm *before* the tool runs. The server parks the batch as pending; `execute` only fires after the visitor's voice (or typed) reply is classified as acceptance. Use for destructive actions. |

## Description writing — the part that matters

The agent is reading dozens of tool descriptions every turn. They
compete for its attention. Three habits:

**1. Lead with the trigger.** Open with *when* to call, not *what* it
does. "Use when the user says 'delete' or refers to removing a record"
beats "Deletes a record."

**2. Name your edge cases.** If the tool overlaps with another, say
so: *"Use this for single-task moves; use `batch_move_tasks` when more
than one task is referenced."*

**3. Defaults belong in the description.** The LLM won't read your
code, so document inline: *"If priority is unspecified, the tool
applies 'medium'."*

## Return values — feed the agent

Whatever `execute` returns is serialized into the agent's next prompt
as a tool result. Give it enough to talk about:

```ts
// Bad — agent can't confirm what happened
execute: async () => ({ ok: true });

// Good — agent can say "I created EX-42 'ship release notes'"
execute: async ({ title }) => ({
    id: "EX-42",
    title,
    status: "created",
    url: "/tasks/EX-42",
});
```

If a tool fails, throw or return `{ error: "..." }` — both surface to
the agent, which then tells the user something went wrong.

## Async + parallel batches

When the agent calls multiple tools in one turn, the widget runs them
**in parallel**. Each tool's `execute` is awaited independently, and
the agent gets all results before its next reply. Don't assume serial
execution between tools in the same batch.

## Tools that need confirmation

```ts
Voqi.defineTool({
    name: "delete_task",
    description: "Permanently delete a task...",
    parameters: { /* ... */ },
    execute: async ({ id }) => myApi.deleteTask(id),
    requiresConfirmation: true,
});
```

The widget surfaces the agent's *intent* to the visitor, who confirms
or cancels by voice ("yes" / "no") or click. The agent waits for the
result before continuing.

Use confirmation for irreversible writes (delete, send, charge). Don't
use it for routine reads or low-risk writes — it adds friction.

## Schema tips

- Always `{ type: "object", properties: {...} }` at the top level.
- `enum` is your friend. The agent's argument-formatting accuracy goes
  up sharply when the schema names the legal values explicitly.
- Add a `description` to each property — the LLM reads them.
- Mark `required` on the fields you can't synthesize a default for.

## Pattern: keep your tools surface narrow

A common mistake is registering one tool per UI action. Don't. The
agent thinks at a higher level than your DOM. A single
`update_task({ id, ...patch })` tool that accepts any subset of fields
will work better than five separate `set_task_priority`,
`set_task_status`, `set_task_assignee`, etc. tools.

Compose at the tool layer; the LLM compresses the user's intent into
a single tool call.

## Reading the example

The richest concrete example lives in
[`examples/tracker/src/voqi.ts`](../examples/tracker/src/voqi.ts).
Read it end-to-end — 30+ tools spanning reads, writes, batches, and
confirmations. Copy the patterns.
