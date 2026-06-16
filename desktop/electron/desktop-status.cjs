"use strict";

const fs = require("node:fs");
const path = require("node:path");

const GPU_OVERRIDE_ON = new Set(["1", "true", "yes", "on"]);
const GPU_OVERRIDE_OFF = new Set(["0", "false", "no", "off"]);

function _safeAppCall(app, method, fallback = "") {
  try {
    if (app && typeof app[method] === "function") return app[method]();
  } catch {
    return fallback;
  }
  return fallback;
}

function candidateInstallStampPaths({
  desktopRoot = path.resolve(__dirname, ".."),
  resourcesPath = process.resourcesPath || "",
} = {}) {
  const candidates = [];
  if (resourcesPath) candidates.push(path.join(resourcesPath, "install-stamp.json"));
  if (desktopRoot) candidates.push(path.join(desktopRoot, "build", "install-stamp.json"));
  return Array.from(new Set(candidates.filter(Boolean)));
}

function readInstallStamp(options = {}) {
  const exists = options.exists || fs.existsSync;
  const readFile = options.readFile || fs.readFileSync;
  for (const candidate of candidateInstallStampPaths(options)) {
    if (!exists(candidate)) continue;
    try {
      const payload = JSON.parse(readFile(candidate, "utf8"));
      return { found: true, path: candidate, payload, error: "" };
    } catch (err) {
      return { found: false, path: candidate, payload: null, error: err.message };
    }
  }
  return { found: false, path: "", payload: null, error: "install stamp not found" };
}

function detectRemoteDisplay({ env = process.env, platform = process.platform } = {}) {
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

function desktopDiagnostics({
  app = null,
  env = process.env,
  versions = process.versions,
  platform = process.platform,
  arch = process.arch,
  desktopRoot = path.resolve(__dirname, ".."),
  resourcesPath = process.resourcesPath || "",
} = {}) {
  const packaged = Boolean(app && app.isPackaged);
  const stamp = readInstallStamp({ desktopRoot, resourcesPath });
  const userDataPath = app && typeof app.getPath === "function" ? _safeAppCall(app, "getPath", "") : "";
  const backendConfigured = Boolean(env.AEGIS_HOME || env.AEGIS_BIN);
  const remoteDisplayReason = detectRemoteDisplay({ env, platform });
  const checks = [
    {
      id: "install_stamp",
      ok: stamp.found,
      severity: stamp.found ? "ok" : "warning",
      detail: stamp.found ? "desktop build stamp is available" : stamp.error,
    },
    {
      id: "backend_environment",
      ok: backendConfigured || !packaged,
      severity: backendConfigured || !packaged ? "ok" : "warning",
      detail: backendConfigured
        ? "AEGIS_HOME or AEGIS_BIN is configured"
        : "packaged desktop will use default backend discovery",
    },
  ];
  return {
    packaged,
    appVersion: _safeAppCall(app, "getVersion", ""),
    platform,
    arch,
    versions: {
      electron: versions.electron || "",
      chrome: versions.chrome || "",
      node: versions.node || "",
    },
    paths: {
      desktopRoot,
      resourcesPath,
      userDataPath,
      installStamp: stamp.path,
    },
    installStamp: stamp.payload,
    renderer: {
      remoteDisplayReason,
      gpuFallbackRecommended: Boolean(remoteDisplayReason),
    },
    checks,
    repair: {
      available: true,
      actions: [
        {
          id: "open_logs",
          label: "Open desktop logs",
          description: "Inspect the desktop backend startup log.",
        },
        {
          id: "restart_backend",
          label: "Restart backend",
          description: "Stop and relaunch the local AEGIS dashboard backend.",
        },
        {
          id: "set_backend_env",
          label: "Set AEGIS_HOME or AEGIS_BIN",
          description: "Point the desktop shell at a known backend install.",
        },
      ],
    },
  };
}

module.exports = {
  candidateInstallStampPaths,
  desktopDiagnostics,
  detectRemoteDisplay,
  readInstallStamp,
};
