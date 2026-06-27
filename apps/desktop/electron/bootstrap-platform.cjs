"use strict";

const fs = require("node:fs");

const GPU_OVERRIDE_ON = new Set(["1", "true", "yes", "on"]);
const GPU_OVERRIDE_OFF = new Set(["0", "false", "no", "off"]);

function isWslEnvironment(env = process.env, platform = process.platform, kernelRelease = null) {
  if (platform !== "linux") return false;
  if (env.WSL_DISTRO_NAME || env.WSL_INTEROP) return true;
  try {
    const release = kernelRelease ?? fs.readFileSync("/proc/sys/kernel/osrelease", "utf8");
    return /microsoft|wsl/i.test(release);
  } catch {
    return false;
  }
}

function isWindowsBinaryPathInWsl(filePath, options = {}) {
  const isWsl = options.isWsl ?? isWslEnvironment(options.env, options.platform);
  if (!isWsl) return false;
  const normalized = String(filePath || "").replace(/\\/g, "/").toLowerCase();
  return [".exe", ".cmd", ".bat", ".ps1"].some((suffix) => normalized.endsWith(suffix));
}

function bundledRuntimeImportCheck(platform = process.platform) {
  return platform === "win32"
    ? "import fastapi, uvicorn, winpty"
    : "import fastapi, uvicorn, ptyprocess";
}

function detectRemoteDisplay(options = {}) {
  const env = options.env ?? process.env;
  const platform = options.platform ?? process.platform;
  const override = String(env.AEGIS_DESKTOP_DISABLE_GPU || "").trim().toLowerCase();
  if (GPU_OVERRIDE_ON.has(override)) return "override (AEGIS_DESKTOP_DISABLE_GPU)";
  if (GPU_OVERRIDE_OFF.has(override)) return null;
  if (env.SSH_CONNECTION || env.SSH_CLIENT || env.SSH_TTY) return "ssh-session";

  if (platform === "linux") {
    const display = String(env.DISPLAY || "");
    if (display.includes(":") && display.split(":")[0]) {
      return `x11-forwarding (DISPLAY=${display})`;
    }
  }
  if (platform === "win32") {
    const sessionName = String(env.SESSIONNAME || "");
    if (/^rdp-/i.test(sessionName)) return `rdp (SESSIONNAME=${sessionName})`;
  }
  return null;
}

module.exports = {
  bundledRuntimeImportCheck,
  detectRemoteDisplay,
  isWindowsBinaryPathInWsl,
  isWslEnvironment,
};
