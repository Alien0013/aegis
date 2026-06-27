import type { InstallerState } from "../store";
import { button } from "../components/button";

export function successRoute(_state: InstallerState): string {
  return `<section class="card success">
    <p class="eyebrow">Complete</p>
    <h1>AEGIS installed</h1>
    <p>Run <code>aegis setup</code> to connect providers, memory, tools, and gateway surfaces.</p>
    <div class="actions">${button("Open setup", "open-setup")}${button("Check updates", "check-updates", "ghost")}</div>
  </section>`;
}
