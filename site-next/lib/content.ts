export const systemStats = [
  { label: "Provider presets", value: "29", detail: "OpenAI-compatible, OAuth-capable, local, and hosted model routes share one config surface." },
  { label: "Registered tools", value: "45", detail: "File, shell, web, browser, LSP, memory, skills, schedules, MCP, and agent-state tools are policy checked." },
  { label: "Bundled skills", value: "41", detail: "Reusable SKILL.md playbooks teach the agent repeatable workflows and verification habits." },
  { label: "Test baseline", value: "973", detail: "Offline regression tests are documented in the project README as the current passing suite." },
];

export const runtimeSteps = [
  "A surface such as CLI, dashboard, desktop, gateway, API, ACP, or MCP submits the task.",
  "The SurfaceRunner builds the request with active profile rules, memory, selected skills, and references.",
  "Agent.run routes the prompt to the chosen provider/model, streams responses, and interprets tool calls.",
  "Every tool call passes permission policy, sensitive-path checks, redaction, and untrusted-result wrapping.",
  "Events, traces, usage, checkpoints, and session rows are persisted for replay, search, rollback, and review.",
];

export const internals = [
  {
    title: "One auditable core",
    body: "Terminal chat, the local dashboard, Electron desktop, messaging bots, OpenAI-compatible API, JSON-RPC, Python SDK, ACP, and MCP all converge on the same Python agent loop. A disabled tool, active memory entry, or permission rule behaves consistently across every surface.",
  },
  {
    title: "Local-first state",
    body: "Configuration, sessions, memory, traces, evals, checkpoints, and tool outputs live under ~/.aegis or $AEGIS_HOME. That makes the system inspectable, backup-friendly, and recoverable without treating a remote service as the source of truth.",
  },
  {
    title: "Skills and memory that improve work",
    body: "AEGIS separates durable user facts from procedural skills. Memory stores stable preferences and environment facts, while skills capture repeatable workflows such as code review, debugging, releases, or document processing.",
  },
  {
    title: "Safety before execution",
    body: "Commands and file operations are mediated by permission modes, hardline blocklists, deny groups, sensitive-file guards, approval flows, sandbox backends, and secret-redaction paths. Tool output is treated as untrusted data, not instructions.",
  },
  {
    title: "Operational feedback loop",
    body: "Traces, eval replay, benchmark tasks, cost analytics, ambient test watching, checkpoints, diffs, and rollback turn each run into evidence. The agent can review completed sessions for safe memory or skill candidates instead of silently drifting.",
  },
  {
    title: "Extensible surfaces",
    body: "Gateway channels, MCP tools, plugins, cron jobs, kanban workers, and the dashboard all plug into shared registries. New capabilities join the same authorization and observability paths instead of becoming one-off side channels.",
  },
];

export const surfaces = ["CLI", "Dashboard", "Desktop", "Gateway bots", "OpenAI API", "Python SDK", "ACP", "MCP"];
