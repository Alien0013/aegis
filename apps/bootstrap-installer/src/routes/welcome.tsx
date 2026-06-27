import type { InstallerState } from "../store";
import { button } from "../components/button";

export function welcomeRoute(state: InstallerState): string {
  const script = state.plan?.script || "install.sh / install.ps1";
  return `<section class="card hero">
    <p class="eyebrow">AEGIS bootstrap</p>
    <h1>Install AEGIS</h1>
    <p>Guided setup for the native one-line installer. The app delegates to the checked-in ${script} flow instead of hiding install logic.</p>
    <div class="actions">${button("Start install", "start-install")}${button("View plan", "show-plan", "ghost")}</div>
  </section>`;
}
