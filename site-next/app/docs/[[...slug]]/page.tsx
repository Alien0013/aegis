import { docsRouteIndex } from "@/lib/content";

type DocsRouteProps = {
  params: Promise<{ slug?: string[] }>;
};

function normalizePath(slug: string[] | undefined) {
  const suffix = (slug || []).join("/").replace(/^\/+|\/+$/g, "");
  return suffix ? `/docs/${suffix}` : "/docs";
}

export default async function DocsRoute({ params }: DocsRouteProps) {
  const { slug } = await params;
  const currentPath = normalizePath(slug);
  const match = docsRouteIndex.find((item) => item.href === currentPath);
  const related = docsRouteIndex.filter((item) => item.href !== currentPath).slice(0, 8);

  return (
    <main>
      <section className="hero compactHero" aria-labelledby="docs-route-title">
        <p className="eyebrow">AEGIS docs route</p>
        <h1 id="docs-route-title">{match?.title || "Documentation"}</h1>
        <p className="lede">
          {match?.body ||
            "This route keeps the public Next.js site and the MkDocs documentation tree mounted under a stable /docs path."}
        </p>
        <div className="heroActions" aria-label="Documentation actions">
          <a href="/" className="button secondary">Back to overview</a>
          <a href="https://github.com/Alien0013/aegis/tree/main/docs" className="button primary">Open source docs</a>
        </div>
      </section>

      <section className="panel" aria-labelledby="docs-route-contract-title">
        <div className="sectionHeading">
          <p className="eyebrow">Routing contract</p>
          <h2 id="docs-route-contract-title">Next and MkDocs share the public docs namespace</h2>
          <p>
            AEGIS keeps narrative pages in the MkDocs tree and exposes this catch-all route so public-site links do not 404
            when the Next surface is served standalone. Deployment can replace this handoff with a static MkDocs mount
            without changing the link contract.
          </p>
        </div>
        <div className="guideGrid">
          {related.map((item) => (
            <a key={item.href} className="linkCard" href={item.href}>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </a>
          ))}
        </div>
      </section>
    </main>
  );
}
