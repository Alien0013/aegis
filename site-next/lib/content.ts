export const systemStats = [
  { label: "Provider presets", value: "29", detail: "OpenAI-compatible, OAuth-capable, local, and hosted model routes share one config surface." },
  { label: "Registered tools", value: "45", detail: "File, shell, web, browser, LSP, memory, skills, schedules, MCP, and agent-state tools are policy checked." },
  { label: "Bundled skills", value: "41", detail: "Reusable SKILL.md playbooks teach the agent repeatable workflows and verification habits." },
  { label: "Test baseline", value: "973+", detail: "Offline regression tests plus generated docs checks are the local proof baseline." },
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

export const documentationPillars = [
  {
    title: "Public docs website",
    href: "/docs",
    body: "The MkDocs tree, Next.js site, generated references, and README-indexed guides are treated as one public documentation surface.",
  },
  {
    title: "Configuration",
    href: "/docs/user-guide/configuration",
    body: "Model routing, provider auth, fallback, auxiliary models, gateway channels, dashboard auth, tools, memory, skills, cron, and approvals.",
  },
  {
    title: "Messaging",
    href: "/docs/user-guide/messaging",
    body: "Gateway setup, fake adapter proof, platform authorization, delivery semantics, attachments, and live-smoke separation.",
  },
  {
    title: "Cron",
    href: "/docs/user-guide/cron",
    body: "Durable scheduled work, prompt jobs, script-assisted jobs, delivery, dry-runs, work directories, and dashboard previews.",
  },
  {
    title: "Sessions",
    href: "/docs/user-guide/sessions",
    body: "Crash recovery, search, lineage, exports, trace links, resume, and session integrity checks.",
  },
  {
    title: "Browser",
    href: "/docs/user-guide/browser",
    body: "Interactive browser automation, local dashboard QA, screenshots, and deterministic HTTP/API alternatives.",
  },
  {
    title: "TTS",
    href: "/docs/user-guide/tts",
    body: "Voice output, voice-message attachments, transcription flow, and platform-specific validation boundaries.",
  },
  {
    title: "Environment Variables",
    href: "/docs/user-guide/environment-variables",
    body: "Credential env vars, live-smoke opt-ins, dashboard token settings, and redaction requirements.",
  },
  {
    title: "Docker",
    href: "/docs/user-guide/docker",
    body: "Clean-container install proof, no host credential bleed, and release verification commands.",
  },
  {
    title: "Hooks",
    href: "/docs/user-guide/hooks",
    body: "Event contract, input shape, auth, rendering, retry, delivery targets, and run metadata.",
  },
  {
    title: "Profile distributions",
    href: "/docs/user-guide/profile-distributions",
    body: "Shareable profile bundles with safe config, skills, tools, model defaults, and credential exclusion.",
  },
  {
    title: "Integration/plugin docs",
    href: "/docs/operations-contracts",
    body: "Plugin, MCP, webhook, memory-provider, gateway-adapter, and platform integration contracts.",
  },
  {
    title: "Operations contracts",
    href: "/docs/operations-contracts",
    body: "Runtime, prompt, tool, gateway, cron, provider, skills, security, and live-QA lifecycle contracts.",
  },
  {
    title: "External live QA",
    href: "/docs/live-qa-matrix",
    body: "Credentialed and OS-runner smoke targets are separate from local fake-adapter proof.",
  },
  {
    title: "File-family depth",
    href: "/docs/maturity",
    body: "Source-path and local-proof rows track depth across runtime, terminal, dashboard, desktop, memory, providers, toolsets, and security.",
  },
];

export const i18nLocales = [
  { locale: "en", label: "English", status: "Canonical", href: "/docs", note: "All generated references and source-linked pages are authored here first." },
  { locale: "fr", label: "Français", status: "Snapshot", href: "/docs/i18n/fr", note: "Install, quickstart, safety, and operator glossary are tracked as a localized launch slice." },
  { locale: "es", label: "Español", status: "Snapshot", href: "/docs/i18n/es", note: "Core setup, surfaces, safety, and live-QA status are documented as a localized snapshot." },
  { locale: "zh-Hans", label: "简体中文", status: "Snapshot", href: "/docs/i18n/zh-Hans", note: "Configuration, tools, gateway, and security topics have a public localization entry point." },
  { locale: "pa", label: "ਪੰਜਾਬੀ", status: "Snapshot", href: "/docs/i18n/pa", note: "Punjabi localization documents the high-level runtime, safety model, and verification flow." },
];

export const developerGuideCards = [
  {
    title: "Adding platform adapters",
    href: "/docs/developer-guide/adding-platform-adapters",
    body: "Adapter lifecycle from message normalization through allowlists, session keys, attachments, fake tests, and live smokes.",
  },
  {
    title: "Plugin LLM access",
    href: "/docs/developer-guide/plugin-llm-access",
    body: "Safe plugin boundaries, provider calls, credential handling, tool registration, and redaction requirements.",
  },
  {
    title: "Session storage",
    href: "/docs/developer-guide/session-storage",
    body: "SQLite session rows, lineage, run links, trace replay, export, search, pruning, and crash recovery.",
  },
  {
    title: "Context compression and caching",
    href: "/docs/developer-guide/context-compression-and-caching",
    body: "Stable/context/volatile prompt material, truncation, compression metadata, and cache-safe extension boundaries.",
  },
  {
    title: "Provider routing",
    href: "/docs/developer-guide/provider-routing",
    body: "Provider registry, capability matrix, auth readiness, credential pools, fallback routing, and auxiliary models.",
  },
  {
    title: "Dashboard and desktop contracts",
    href: "/docs/developer-guide/dashboard-desktop-contracts",
    body: "FastAPI routes, WebSocket tickets, React dashboard state, Electron backend readiness, and cross-OS installer proof.",
  },
  {
    title: "Security approvals",
    href: "/docs/developer-guide/security-approvals",
    body: "Command approvals, yolo boundaries, redaction, file safety, dashboard token minimization, and gateway authorization.",
  },
];

export const liveQaHighlights = [
  "External live QA is never implied by local fake adapters.",
  "Messaging channels require real bot/webhook/account credentials before they can be called live-ready.",
  "Providers require real API-key or OAuth smoke tests before billing/auth readiness is claimed.",
  "Desktop parity requires Linux, Windows, and macOS installer/open/update/uninstall evidence.",
  "Every live proof records sanitized command, date, commit SHA, and failure reason without credentials.",
];
