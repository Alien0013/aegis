export type InstallerRoute = "welcome" | "progress" | "success" | "failure";

export interface InstallPlan {
  platform: "linux" | "macos" | "windows" | "unknown";
  script: "install.sh" | "install.ps1";
  command: string;
  updateCommand: string;
  setupCommand: string;
}

export interface InstallerState {
  route: InstallerRoute;
  plan?: InstallPlan;
  log: string[];
  error?: string;
}

export const initialState: InstallerState = {
  route: "welcome",
  log: ["Ready to install AEGIS."],
};

export function withRoute(state: InstallerState, route: InstallerRoute): InstallerState {
  return { ...state, route };
}

export function appendLog(state: InstallerState, line: string): InstallerState {
  return { ...state, log: [...state.log, line] };
}
