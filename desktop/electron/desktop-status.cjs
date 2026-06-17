"use strict";

const fs = require("node:fs");
const path = require("node:path");
const {
  candidatePackagedAegisCommands,
  packagedBackendPathEntries,
} = require("./backend-env.cjs");

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

function releaseUpdateEligibility({
  packaged = false,
  stamp = null,
  platform = process.platform,
} = {}) {
  if (!packaged) {
    return { ok: false, reason: "auto-update runs in the installed app only" };
  }
  if (!stamp || !stamp.found || !stamp.payload) {
    return { ok: false, reason: stamp && stamp.error ? stamp.error : "install stamp not found" };
  }
  const payload = stamp.payload;
  if (Number(payload.schemaVersion || 0) < 2) {
    return { ok: false, reason: "install stamp is too old for safe updates" };
  }
  if (!payload.release || !payload.trustedRelease) {
    return { ok: false, reason: "installed package is not stamped as a trusted release" };
  }
  if (payload.dirty) {
    return { ok: false, reason: "installed package was built from a dirty worktree" };
  }
  const targets = Array.isArray(payload.targetPlatforms) ? payload.targetPlatforms : [];
  if (targets.length && !targets.includes(platform)) {
    return { ok: false, reason: `install stamp target does not match ${platform}` };
  }
  return { ok: true, reason: "installed package is eligible for auto-update" };
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
  exists = fs.existsSync,
} = {}) {
  const packaged = Boolean(app && app.isPackaged);
  const stamp = readInstallStamp({ desktopRoot, resourcesPath });
  const updateEligibility = releaseUpdateEligibility({ packaged, stamp, platform });
  const userDataPath = app && typeof app.getPath === "function" ? _safeAppCall(app, "getPath", "") : "";
  const appPath = app && typeof app.getAppPath === "function" ? _safeAppCall(app, "getAppPath", "") : "";
  const packagedBackendCandidates = packaged
    ? candidatePackagedAegisCommands({ platform, resourcesPath, appPath })
    : [];
  const packagedPathEntries = packaged
    ? packagedBackendPathEntries({ platform, resourcesPath, appPath, exists })
    : [];
  const bundledBackend = packagedBackendCandidates.some((candidate) => {
    try { return exists(candidate); } catch { return false; }
  });
  const backendConfigured = Boolean(env.AEGIS_HOME || env.AEGIS_BIN || bundledBackend);
  const remoteDisplayReason = detectRemoteDisplay({ env, platform });
  const checks = [
    {
      id: "install_stamp",
      ok: stamp.found,
      severity: stamp.found ? "ok" : "warning",
      detail: stamp.found ? "desktop build stamp is available" : stamp.error,
    },
    {
      id: "release_update_eligibility",
      ok: updateEligibility.ok,
      severity: updateEligibility.ok || !packaged ? "ok" : "warning",
      detail: updateEligibility.reason,
    },
    {
      id: "backend_environment",
      ok: backendConfigured || !packaged,
      severity: backendConfigured || !packaged ? "ok" : "warning",
      detail: backendConfigured
        ? (bundledBackend ? "packaged backend candidate is bundled" : "AEGIS_HOME or AEGIS_BIN is configured")
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
    updateEligibility,
    renderer: {
      remoteDisplayReason,
      gpuFallbackRecommended: Boolean(remoteDisplayReason),
    },
    backendDiscovery: {
      configured: backendConfigured,
      bundled: bundledBackend,
      packagedCandidates: packagedBackendCandidates,
      packagedPathEntries,
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
  releaseUpdateEligibility,
};
