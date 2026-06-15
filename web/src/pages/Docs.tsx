import { Badge, Card, PageHeader } from "../components/ui";

const sections = [
  {
    title: "Harness",
    items: [
      ["Dashboard", "FastAPI serves the React control plane and protects API routes with local auth."],
      ["Desktop", "Electron launches or connects to the local dashboard backend and wraps the same runtime."],
      ["Sessions", "SQLite stores conversations, tool events, metadata, and search state."],
    ],
  },
  {
    title: "Agent",
    items: [
      ["Models", "Provider settings, model selection, probes, and visibility controls."],
      ["Tools", "Tool registry, toolsets, availability, and enable/disable controls."],
      ["Skills", "Reusable SKILL.md procedures, bundles, categories, marketplace search, and install flow."],
      ["Memory", "USER.md and MEMORY.md with durable user and environment context."],
    ],
  },
  {
    title: "Operations",
    items: [
      ["Cron", "Durable scheduled agent jobs with skills, scripts, delivery, and chaining support."],
      ["Gateway", "Messaging channels, webhooks, pairing, delivery targets, and gateway status."],
      ["Profiles", "Isolated runtime homes for config, secrets, skills, sessions, cron, and memory."],
      ["Logs", "Agent, GUI, gateway, desktop, and error log inspection."],
    ],
  },
  {
    title: "Security",
    items: [
      ["Secrets", "Environment values are redacted in the UI and stored in profile env files."],
      ["Untrusted Input", "Files, web pages, terminal output, browser snapshots, and messages remain untrusted."],
      ["Desktop Bridge", "Native operations belong in Electron main/preload, not the renderer."],
    ],
  },
];

export function Docs() {
  return (
    <>
      <PageHeader title="Docs" sub="Dashboard and desktop harness map" />
      <div className="grid gap-[var(--gap)] md:grid-cols-2">
        {sections.map((section) => (
          <Card key={section.title} title={section.title}>
            <div className="space-y-3">
              {section.items.map(([name, text]) => (
                <div key={name}>
                  <Badge tone="neutral">{name}</Badge>
                  <p className="mt-1 text-sm text-dim">{text}</p>
                </div>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}
