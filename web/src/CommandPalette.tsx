import { useEffect, useMemo, useState } from "react";
import { post } from "./lib/api";
import { Icon } from "./lib/icons";

type Command = {
  id: string;
  title: string;
  group: string;
  hint: string;
  run: () => void | Promise<void>;
};

export function CommandPalette({
  open,
  onClose,
  go,
  reload,
}: {
  open: boolean;
  onClose: () => void;
  go: (id: string) => void;
  reload?: () => void;
}) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");

  function nav(id: string) {
    go(id);
    onClose();
  }

  async function setConfig(key: string, value: any, label: string) {
    setBusy(label);
    try {
      await post("config", { key, value });
      reload?.();
      onClose();
    } finally {
      setBusy("");
    }
  }

  async function postAction(path: string, body: any, label: string) {
    setBusy(label);
    try {
      await post(path, body);
      reload?.();
      onClose();
    } finally {
      setBusy("");
    }
  }

  const commands: Command[] = [
    { id: "overview", title: "Open Home", group: "Navigate", hint: "Operator overview", run: () => nav("overview") },
    { id: "agents", title: "Open Agents", group: "Navigate", hint: "Live spawned agents", run: () => nav("agents") },
    { id: "chat", title: "Open Chat", group: "Navigate", hint: "Full thread", run: () => nav("chat") },
    { id: "kanban", title: "Open Kanban", group: "Navigate", hint: "Task board", run: () => nav("kanban") },
    { id: "memory", title: "Open Memory", group: "Navigate", hint: "USER and MEMORY facts", run: () => nav("memory") },
    { id: "tools", title: "Open Tool Manager", group: "Navigate", hint: "Schemas and enabled tools", run: () => nav("tools") },
    { id: "settings", title: "Open Settings", group: "Navigate", hint: "Quick and advanced config", run: () => nav("config") },
    { id: "system", title: "Open System", group: "Navigate", hint: "Logs, backups, checkpoints", run: () => nav("system") },
    { id: "projects", title: "Open Projects", group: "Navigate", hint: "Workspaces", run: () => nav("projects") },
    { id: "runs", title: "Open Runs", group: "Navigate", hint: "Recent agent runs", run: () => nav("runs") },
    { id: "traces", title: "Open Traces", group: "Navigate", hint: "Tool/model spans", run: () => nav("traces") },
    { id: "reasoning-live", title: "Reasoning: Live", group: "Agent", hint: "Stream thinking when available", run: () => setConfig("display.reasoning", "live", "reasoning-live") },
    { id: "reasoning-summary", title: "Reasoning: Summary", group: "Agent", hint: "Compact thinking indicator", run: () => setConfig("display.reasoning", "summary", "reasoning-summary") },
    { id: "reasoning-off", title: "Reasoning: Off", group: "Agent", hint: "Hide reasoning stream", run: () => setConfig("display.reasoning", "off", "reasoning-off") },
    { id: "perm-ask", title: "Permissions: Ask", group: "Security", hint: "Prompt for risky tools", run: () => setConfig("tools.exec_mode", "ask", "perm-ask") },
    { id: "perm-auto", title: "Permissions: Auto", group: "Security", hint: "Auto-approve within sandbox", run: () => setConfig("tools.exec_mode", "auto", "perm-auto") },
    { id: "perm-deny", title: "Permissions: Deny", group: "Security", hint: "Block grouped tools", run: () => setConfig("tools.exec_mode", "deny", "perm-deny") },
    { id: "run-board", title: "Run Kanban Board", group: "Operate", hint: "Start board runner", run: () => postAction("kanban", { action: "run" }, "run-board") },
    { id: "backup", title: "Create Backup", group: "Recovery", hint: "Snapshot AEGIS state", run: () => postAction("system", { action: "backup" }, "backup") },
  ];

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return commands;
    return commands.filter((c) => `${c.title} ${c.group} ${c.hint}`.toLowerCase().includes(needle));
  }, [q]);

  useEffect(() => {
    if (open) setQ("");
  }, [open]);

  if (!open) return null;

  return (
    <div className="cmdscrim" role="dialog" aria-label="Command palette" onClick={onClose}>
      <div className="cmdbox" onClick={(e) => e.stopPropagation()}>
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
          placeholder="Search commands, settings, tools…" />
        <div className="cmdlist">
          {!visible.length && <div className="empty small">No commands match.</div>}
          {visible.map((cmd) => (
            <button className="cmditem" key={cmd.id} onClick={() => void cmd.run()} disabled={Boolean(busy)}>
              <span style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0, textAlign: "left" }}>
                <b style={{ fontWeight: 600, fontSize: 13 }}>{cmd.title}</b>
                <small className="faint" style={{ fontSize: 11 }}>{cmd.hint}</small>
              </span>
              <span className="grp">{busy === cmd.id ? "running…" : cmd.group}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
