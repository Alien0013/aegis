import { useEffect, useMemo, useState } from "react";
import { api, apiDelete, patch, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, Loading, PageHeader, useToast } from "../lib/ui";

export function SkillsPage() {
  const [payload, setPayload] = useState<any>(null);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const [content, setContent] = useState("");
  const [q, setQ] = useState("");
  const [marketQ, setMarketQ] = useState("");
  const [market, setMarket] = useState<any[]>([]);
  const [source, setSource] = useState("");
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [busy, setBusy] = useState("");
  const toast = useToast();

  async function load(next = selected) {
    const p = await api("skills/manage");
    setPayload(p);
    if (next) await openSkill(next);
  }

  async function openSkill(name: string) {
    setSelected(name);
    const d = await api(`skills/${encodeURIComponent(name)}`);
    setDetail(d);
    setContent(d.content || "");
  }

  useEffect(() => { load("").catch((e) => toast(String(e), "err")); }, []);

  const skills: any[] = payload?.skills || [];
  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return skills;
    return skills.filter((s) => `${s.name} ${s.description} ${s.source}`.toLowerCase().includes(query));
  }, [skills, q]);

  async function saveSkill() {
    if (!selected) return;
    try {
      await patch(`skills/${encodeURIComponent(selected)}`, { content });
      toast("Skill saved", "ok");
      await load(selected);
    } catch (e) {
      toast(String(e), "err");
    }
  }

  async function deleteSkill() {
    if (!selected) return;
    try {
      await apiDelete(`skills/${encodeURIComponent(selected)}`);
      toast(`${selected} deleted`, "ok");
      setSelected("");
      setDetail(null);
      setContent("");
      await load("");
    } catch (e) {
      toast(String(e), "err");
    }
  }

  async function createSkill() {
    if (!newName.trim() || !newDescription.trim()) return;
    await post("skills", {
      name: newName.trim(),
      description: newDescription.trim(),
      body: "Use this skill when its description matches the task.\n",
    });
    toast(`${newName} created`, "ok");
    const name = newName.trim();
    setNewName("");
    setNewDescription("");
    await load(name);
  }

  async function searchMarketplace() {
    setBusy("search");
    try {
      const r = await api(`skills/marketplace/search?q=${encodeURIComponent(marketQ)}`);
      setMarket(r.results || []);
    } finally { setBusy(""); }
  }

  async function installSkill(body: any) {
    setBusy("install");
    try {
      const r = await post("skills/marketplace/install", body);
      toast(`Installed ${(r.installed || []).join(", ") || "skill"}`, "ok");
      await load("");
    } finally { setBusy(""); }
  }

  if (!payload) return <><PageHeader title="Skills" /><Loading /></>;

  return (
    <>
      <PageHeader
        title="Skills"
        sub={`${payload.count || 0} available · ${Object.keys(payload.installed || {}).length} marketplace-installed`}
      />
      <div className="stack">
        <div className="grid c3">
          <Card title="Create personal skill">
            <div className="stack">
              <Field label="Name"><input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="release-review" /></Field>
              <Field label="Description"><input value={newDescription} onChange={(e) => setNewDescription(e.target.value)} placeholder="Use when preparing a release." /></Field>
              <Button onClick={createSkill} icon="plus">Create</Button>
            </div>
          </Card>
          <Card title="Install from source">
            <div className="stack">
              <Field label="Source"><input value={source} onChange={(e) => setSource(e.target.value)} placeholder="git:owner/repo or https://.../SKILL.md" /></Field>
              <Button onClick={() => installSkill({ source })} disabled={busy === "install"} icon="plus">Install</Button>
            </div>
          </Card>
          <Card title="Hub taps">
            <div className="pill-list">
              {Object.entries(payload.taps || {}).map(([name, repo]) => <span className="pill" key={name}>{name}: <span className="mono">{String(repo)}</span></span>)}
            </div>
          </Card>
        </div>

        <div className="grid c2">
          <Card title="Installed & bundled" actions={<input className="search compact" placeholder="Search skills" value={q} onChange={(e) => setQ(e.target.value)} />} pad={false}>
            {!filtered.length && <Empty small>No skills match.</Empty>}
            <div style={{ padding: filtered.length ? "2px 14px 6px" : 0, maxHeight: "56vh", overflow: "auto" }}>
              {filtered.map((s) => (
                <div className="row click" key={s.name} onClick={() => openSkill(s.name)}>
                  <span style={{ minWidth: 0 }}>
                    <b>{s.name}</b> <span className="mut">{s.description}</span>
                    <div className="pill-list">
                      <Badge status={s.available ? "ready" : "missing"}>{s.available ? "ready" : "blocked"}</Badge>
                      <span className="pill">tier {s.tier}</span>
                      {s.installed && <span className="pill">marketplace</span>}
                    </div>
                  </span>
                  <span className="mono mut">{(s.usage?.count || 0)} uses</span>
                </div>
              ))}
            </div>
          </Card>

          <Card title={selected ? `Inspect ${selected}` : "Inspect skill"} actions={selected && detail?.skill?.editable && <span className="actions"><Button sm onClick={saveSkill} icon="check">Save</Button><Button sm variant="danger" onClick={deleteSkill}>Delete</Button></span>}>
            {!selected && <Empty small>Select a skill to inspect its package.</Empty>}
            {selected && <>
              <div className="pill-list">
                <span className="pill mono">{detail?.skill?.path}</span>
                <Badge status={detail?.skill?.available ? "ready" : "missing"}>{detail?.skill?.available ? "ready" : "blocked"}</Badge>
                <Badge status={detail?.skill?.editable ? "active" : "idle"}>{detail?.skill?.editable ? "editable" : "inspect-only"}</Badge>
              </div>
              <textarea className="profile-editor mono" rows={18} value={content} onChange={(e) => setContent(e.target.value)} readOnly={!detail?.skill?.editable} />
            </>}
          </Card>
        </div>

        <Card title="Marketplace search" actions={<span className="actions"><input className="search compact" placeholder="Search registry" value={marketQ} onChange={(e) => setMarketQ(e.target.value)} /><Button sm onClick={searchMarketplace} disabled={busy === "search"} icon="search">Search</Button></span>} pad={false}>
          {!market.length && <Empty small>Search the skill registry or install directly from a source.</Empty>}
          <div className="market-grid">
            {market.map((m, i) => (
              <div className="market-card" key={`${m.name}-${i}`}>
                <div className="channel-top">
                  <div><b>{m.name}</b><div className="mut">{m.description}</div></div>
                  <Badge status="idle">registry</Badge>
                </div>
                <div className="mono smalltext">{m.source}</div>
                <Button sm icon="plus" disabled={busy === "install"} onClick={() => installSkill({ source: m.source })}>Install</Button>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
