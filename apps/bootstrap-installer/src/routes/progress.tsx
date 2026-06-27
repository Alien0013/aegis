import type { InstallerState } from "../store";
import { escapeHtml } from "../lib/utils";

export function progressRoute(state: InstallerState): string {
  const lines = state.log.map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  return `<section class="card">
    <p class="eyebrow">Installing</p>
    <h1>Running native installer</h1>
    <p>Keep this window open while AEGIS prepares its Python environment and desktop hooks.</p>
    <ol class="log">${lines}</ol>
  </section>`;
}
