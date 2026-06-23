import { internals, runtimeSteps, surfaces, systemStats } from "@/lib/content";

export default function Home() {
  return (
    <main>
      <section className="hero" aria-labelledby="hero-title">
        <p className="eyebrow">AEGIS internals · built with Next.js</p>
        <h1 id="hero-title">The terminal AI agent you can inspect, steer, and own.</h1>
        <p className="lede">
          AEGIS is not just a chat box. It is a local-first agent harness where every surface shares one runtime,
          one permission model, one memory layer, and one auditable trail of tool use.
        </p>
        <div className="heroActions" aria-label="Primary actions">
          <a href="#runtime" className="button primary">Explore the runtime</a>
          <a href="#internals" className="button secondary">See the internals</a>
        </div>
      </section>

      <section className="stats" aria-label="AEGIS system facts">
        {systemStats.map((stat) => (
          <article key={stat.label} className="statCard">
            <span className="statValue">{stat.value}</span>
            <h2>{stat.label}</h2>
            <p>{stat.detail}</p>
          </article>
        ))}
      </section>

      <section className="panel" id="runtime" aria-labelledby="runtime-title">
        <div className="sectionHeading">
          <p className="eyebrow">Runtime path</p>
          <h2 id="runtime-title">What happens when you ask AEGIS to work harder</h2>
          <p>
            The same loop handles terminal sessions, dashboard chats, scheduled jobs, gateway messages, and API calls.
            That consistency is what makes the system debuggable instead of mysterious.
          </p>
        </div>
        <ol className="timeline">
          {runtimeSteps.map((step, index) => (
            <li key={step}>
              <span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
              <p>{step}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="panel split" aria-labelledby="surfaces-title">
        <div>
          <p className="eyebrow">Shared surfaces</p>
          <h2 id="surfaces-title">Many doors, one agent core</h2>
          <p>
            Each surface is a different way to reach the same governed runtime. The website, CLI, local dashboard,
            background automation, and external integrations do not need separate personalities or hidden state.
          </p>
        </div>
        <ul className="surfaceGrid" aria-label="Supported AEGIS surfaces">
          {surfaces.map((surface) => <li key={surface}>{surface}</li>)}
        </ul>
      </section>

      <section className="internals" id="internals" aria-labelledby="internals-title">
        <div className="sectionHeading">
          <p className="eyebrow">Inside the harness</p>
          <h2 id="internals-title">The parts that make AEGIS informative and controllable</h2>
        </div>
        <div className="cardGrid">
          {internals.map((item) => (
            <article key={item.title} className="infoCard">
              <h3>{item.title}</h3>
              <p>{item.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="callout" aria-labelledby="ship-title">
        <p className="eyebrow">Shippable story</p>
        <h2 id="ship-title">Transparent by default, extensible when needed</h2>
        <p>
          This Next.js page explains the runtime in plain language while keeping the claims grounded in the repository:
          shared surfaces, local state, provider routing, guarded tools, memory, skills, traces, evals, and rollback.
        </p>
      </section>
    </main>
  );
}
