import { failureRoute } from "./routes/failure";
import { progressRoute } from "./routes/progress";
import { successRoute } from "./routes/success";
import { welcomeRoute } from "./routes/welcome";
import { appendLog, initialState, type InstallerState, withRoute } from "./store";
import "./styles.css";

type BootstrapApi = {
  bootstrap_plan?: () => Promise<InstallerState["plan"]>;
  run_bootstrap_install?: () => Promise<{ ok: boolean; log?: string[]; error?: string }>;
  open_update_docs?: () => Promise<void>;
};

const api: BootstrapApi = (window as unknown as { __TAURI__?: BootstrapApi }).__TAURI__ || {};
let state: InstallerState = initialState;

function renderRoute(current: InstallerState): string {
  if (current.route === "progress") return progressRoute(current);
  if (current.route === "success") return successRoute(current);
  if (current.route === "failure") return failureRoute(current);
  return welcomeRoute(current);
}

function setState(next: InstallerState): void {
  state = next;
  const root = document.getElementById("root");
  if (!root) return;
  root.innerHTML = `<main>${renderRoute(state)}</main>`;
  bindActions(root);
}

async function startInstall(): Promise<void> {
  setState(appendLog(withRoute(state, "progress"), "Starting native AEGIS installer..."));
  try {
    const result = await api.run_bootstrap_install?.();
    if (result?.ok) {
      setState({ ...state, route: "success", log: result.log || ["Installer completed successfully."] });
    } else {
      setState({ ...state, route: "failure", error: result?.error || "Install command failed." });
    }
  } catch (error) {
    setState({ ...state, route: "failure", error: String(error) });
  }
}

function bindActions(root: HTMLElement): void {
  root.querySelector("#start-install")?.addEventListener("click", () => void startInstall());
  root.querySelector("#show-plan")?.addEventListener("click", () => setState(appendLog(state, state.plan?.command || "Install plan unavailable.")));
  root.querySelector("#check-updates")?.addEventListener("click", () => void api.open_update_docs?.());
}

export async function mountInstaller(): Promise<void> {
  try {
    state = { ...state, plan: await api.bootstrap_plan?.() };
  } catch {
    state = { ...state, log: [...state.log, "Plan probe unavailable; install script can still be run manually."] };
  }
  setState(state);
}
