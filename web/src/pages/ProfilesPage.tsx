import { useEffect, useMemo, useState } from "react";
import { api, apiDelete, patch, post } from "../lib/api";
import { Badge, Button, Card, Empty, Field, Loading, PageHeader, useToast } from "../lib/ui";

export function ProfilesPage() {
  const [payload, setPayload] = useState<any>(null);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const [content, setContent] = useState("");
  const [name, setName] = useState("");
  const [q, setQ] = useState("");
  const toast = useToast();

  async function load(next = selected) {
    const p = await api("profiles");
    setPayload(p);
    const chosen = next || p.active || p.available?.[0] || "";
    setSelected(chosen);
    if (chosen) {
      const d = await api(`profiles/${encodeURIComponent(chosen)}`);
      setDetail(d);
      setContent(d.content || "");
    } else {
      setDetail(null);
      setContent("");
    }
  }

  useEffect(() => { load().catch((e) => toast(String(e), "err")); }, []);

  const profiles: any[] = payload?.profiles || [];
  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return query ? profiles.filter((p) => p.name.toLowerCase().includes(query)) : profiles;
  }, [profiles, q]);

  async function activate(profile = selected) {
    if (!profile) {
      await post("profiles", { name: "" });
      toast("Default profile active", "ok");
      await load("");
      return;
    }
    await post(`profiles/${encodeURIComponent(profile)}/activate`, {});
    toast(`${profile} active`, "ok");
    await load(profile);
  }

  async function save() {
    if (!selected) return;
    await patch(`profiles/${encodeURIComponent(selected)}`, { content });
    toast("Profile saved", "ok");
    await load(selected);
  }

  async function create() {
    const clean = name.trim();
    if (!clean) return;
    await post("profiles", { name: clean, content: `# ${clean}\n\n`, activate: true });
    setName("");
    toast(`${clean} created`, "ok");
    await load(clean);
  }

  async function remove() {
    if (!selected) return;
    await apiDelete(`profiles/${encodeURIComponent(selected)}`);
    toast(`${selected} deleted`, "ok");
    await load("");
  }

  if (!payload) return <><PageHeader title="Profiles" /><Loading /></>;

  return (
    <>
      <PageHeader
        title="Profiles"
        sub={<><Badge status={payload.active ? "active" : "idle"}>{payload.active || "default"}</Badge> <span className="mono">{payload.path}</span></>}
        actions={<Button onClick={() => activate("")} variant="ghost">Use default</Button>}
      />
      <div className="grid c2">
        <div className="stack">
          <Card title="Create profile">
            <div className="grid c2" style={{ alignItems: "end" }}>
              <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="support-ops" /></Field>
              <Button onClick={create} icon="plus">Create</Button>
            </div>
          </Card>
          <Card title="Profiles" actions={<input className="search compact" placeholder="Search profiles" value={q} onChange={(e) => setQ(e.target.value)} />} pad={false}>
            {!filtered.length && <Empty small>No profiles yet.</Empty>}
            <div style={{ padding: filtered.length ? "2px 14px 6px" : 0 }}>
              {filtered.map((p) => (
                <div className="row click" key={p.name} onClick={() => load(p.name)}>
                  <span><b>{p.name}</b><span className="mut mono"> {p.path}</span></span>
                  <span className="actions">
                    <Badge status={p.active ? "active" : "idle"}>{p.active ? "active" : "ready"}</Badge>
                    <Button sm variant="ghost" onClick={(e) => { e.stopPropagation(); activate(p.name); }}>Activate</Button>
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <Card title={selected ? `Edit ${selected}` : "Profile editor"} actions={selected && <span className="actions"><Button sm onClick={save} icon="check">Save</Button><Button sm variant="danger" onClick={remove}>Delete</Button></span>}>
          {!selected && <Empty small>Select or create a profile.</Empty>}
          {selected && <>
            <div className="pill-list">
              <span className="pill mono">{detail?.path}</span>
              <Badge status={detail?.active ? "active" : "idle"}>{detail?.active ? "active" : "inactive"}</Badge>
            </div>
            <textarea className="profile-editor" rows={18} value={content} onChange={(e) => setContent(e.target.value)} />
          </>}
        </Card>
      </div>
    </>
  );
}
