import type { InstallerState } from "../store";
import { button } from "../components/button";
import { escapeHtml } from "../lib/utils";

export function failureRoute(state: InstallerState): string {
  return `<section class="card failure">
    <p class="eyebrow">Install needs attention</p>
    <h1>Bootstrap failed</h1>
    <p>${escapeHtml(state.error || "The native installer returned a non-zero exit code.")}</p>
    <div class="actions">${button("Retry", "start-install")}${button("Copy logs", "copy-logs", "ghost")}</div>
  </section>`;
}
