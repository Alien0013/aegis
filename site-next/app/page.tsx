import {
  developerGuideCards,
  documentationPillars,
  i18nLocales,
  internals,
  liveQaHighlights,
  runtimeSteps,
  surfaces,
  systemStats,
} from "@/lib/content";

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
          <a href="#docs" className="button secondary">Open the docs matrix</a>
          <a href="#i18n" className="button secondary">See localization</a>
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

      <section className="panel" id="docs" aria-labelledby="docs-title">
        <div className="sectionHeading">
          <p className="eyebrow">Public documentation parity</p>
          <h2 id="docs-title">A real docs surface, not a README pile</h2>
          <p>
            The public docs site now carries a single navigable map for user guides, generated references,
            integration/plugin docs, operations contracts, live QA, and file-family maturity evidence.
          </p>
        </div>
        <div className="docGrid">
          {documentationPillars.map((pillar) => (
            <a key={pillar.title} className="linkCard" href={pillar.href}>
              <h3>{pillar.title}</h3>
              <p>{pillar.body}</p>
            </a>
          ))}
        </div>
      </section>

      <section className="panel" id="i18n" aria-labelledby="i18n-title">
        <div className="sectionHeading">
          <p className="eyebrow">Internationalization</p>
          <h2 id="i18n-title">Localization is tracked as a docs contract</h2>
          <p>
            English remains canonical, while localized snapshot pages make public translation status explicit
            for readers and contributors instead of hiding i18n work in an issue backlog.
          </p>
        </div>
        <div className="localeGrid">
          {i18nLocales.map((locale) => (
            <a key={locale.locale} className="linkCard localeCard" href={locale.href}>
              <span>{locale.locale}</span>
              <h3>{locale.label}</h3>
              <p><strong>{locale.status}.</strong> {locale.note}</p>
            </a>
          ))}
        </div>
      </section>

      <section className="panel" id="developer-guides" aria-labelledby="developer-guides-title">
        <div className="sectionHeading">
          <p className="eyebrow">Developer guide</p>
          <h2 id="developer-guides-title">Contracts for extending AEGIS safely</h2>
          <p>
            Platform adapters, plugins, session storage, prompt context, providers, dashboard/desktop,
            and security approvals now have first-class developer-guide entry points.
          </p>
        </div>
        <div className="guideGrid">
          {developerGuideCards.map((guide) => (
            <a key={guide.title} className="linkCard" href={guide.href}>
              <h3>{guide.title}</h3>
              <p>{guide.body}</p>
            </a>
          ))}
        </div>
      </section>

      <section className="callout" id="live-qa" aria-labelledby="live-qa-title">
        <p className="eyebrow">Live QA truthfulness</p>
        <h2 id="live-qa-title">Local proof and external proof are separate by design</h2>
        <p>
          AEGIS can prove local contracts continuously, but it does not call a Telegram bot, provider account,
          SMS bridge, or macOS installer live-ready until a credentialed or OS-runner smoke records evidence.
        </p>
        <ul className="qaGrid">
          {liveQaHighlights.map((highlight) => <li key={highlight}>{highlight}</li>)}
        </ul>
      </section>

      <section className="callout" aria-labelledby="ship-title">
        <p className="eyebrow">Shippable story</p>
        <h2 id="ship-title">Transparent by default, extensible when needed</h2>
        <p>
          This Next.js page explains the runtime in plain language while keeping the claims grounded in the repository:
          shared surfaces, local state, provider routing, guarded tools, memory, skills, traces, evals, rollback,
          public docs, i18n status, and live-QA evidence boundaries.
        </p>
      </section>
    </main>
  );
}
