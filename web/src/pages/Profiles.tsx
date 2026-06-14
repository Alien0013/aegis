import { post } from "../lib/api";
import { useApi } from "../lib/useApi";
import { cn } from "../lib/cn";
import { Badge, Button, Card, Empty, Loading, PageHeader, toast } from "../components/ui";

interface ProfilesPayload {
  active?: string;
  available?: string[];
  profiles?: { name: string; active: boolean }[];
  path?: string;
}

export function Profiles() {
  const { data, loading, error, reload } = useApi<ProfilesPayload>("profiles");
  const active = data?.active || "";
  const list = data?.profiles || (data?.available || []).map((n) => ({ name: n, active: n === active }));

  async function activate(name: string) {
    try { await post("profiles", { name }); toast(name ? `Activated ${name}` : "Cleared profile"); reload(); }
    catch (e) { toast(String(e), "err"); }
  }

  return (
    <>
      <PageHeader title="Profiles" sub={data?.path || "Personality profiles (workspace/personalities/*.md)"}
        actions={active ? <Button variant="ghost" icon="x" onClick={() => activate("")}>Clear</Button> : undefined} />
      {error && <Card><Empty icon="alert">Couldn't load — {error}</Empty></Card>}
      {loading && <Loading />}
      {data && (
        <Card pad={false}>
          {!list.length && <Empty icon="profiles">No profiles. Add a .md file under workspace/personalities/.</Empty>}
          {list.map((p) => (
            <div key={p.name} className={cn("flex items-center gap-3 border-b border-border px-[var(--pad)] py-2.5 last:border-0",
              p.active && "bg-primary/5")}>
              <div className="min-w-0 flex-1">
                <span className="font-mono text-sm text-text">{p.name}</span>
              </div>
              {p.active ? <Badge status="active">active</Badge>
                : <Button sm variant="ghost" onClick={() => activate(p.name)}>Activate</Button>}
            </div>
          ))}
        </Card>
      )}
    </>
  );
}
