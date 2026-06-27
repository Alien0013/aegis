"use strict";

const fs = require("node:fs");
const path = require("node:path");
const {
  candidatePackagedAegisCommands,
  packagedBackendPathEntries,
  resolveAegisCommand,
} = require("./backend-env.cjs");
const { desktopUninstallPlan } = require("./desktop-uninstall.cjs");

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

function candidateBackendManifestPaths({
  desktopRoot = path.resolve(__dirname, ".."),
  resourcesPath = process.resourcesPath || "",
} = {}) {
  const candidates = [];
  if (resourcesPath) candidates.push(path.join(resourcesPath, "backend-manifest.json"));
  if (desktopRoot) candidates.push(path.join(desktopRoot, "build", "backend-manifest.json"));
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

function readBackendManifest(options = {}) {
  const exists = options.exists || fs.existsSync;
  const readFile = options.readFile || fs.readFileSync;
  for (const candidate of candidateBackendManifestPaths(options)) {
    if (!exists(candidate)) continue;
    try {
      const payload = JSON.parse(readFile(candidate, "utf8"));
      return { found: true, path: candidate, payload, error: "" };
    } catch (err) {
      return { found: false, path: candidate, payload: null, error: err.message };
    }
  }
  return { found: false, path: "", payload: null, error: "backend manifest not found" };
}

function releaseUpdateEligibility({
  packaged = false,
  stamp = null,
  backendManifest = null,
  platform = process.platform,
  appVersion = "",
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
  if (payload.appVersion && appVersion && String(payload.appVersion) !== String(appVersion)) {
    return {
      ok: false,
      reason: `install stamp app version ${payload.appVersion} does not match running app ${appVersion}`,
    };
  }
  const targets = Array.isArray(payload.targetPlatforms) ? payload.targetPlatforms : [];
  if (targets.length && !targets.includes(platform)) {
    return { ok: false, reason: `install stamp target does not match ${platform}` };
  }
  if (backendManifest) {
    if (!backendManifest.found || !backendManifest.payload) {
      return { ok: false, reason: backendManifest.error || "backend manifest not found" };
    }
    const backend = backendManifest.payload;
    if (!backend.staged) {
      if (backend.externalBackend || backend.mode === "external") {
        return { ok: false, reason: "external-backend desktop releases are not auto-update eligible" };
      }
      return { ok: false, reason: "packaged backend was not staged for this release" };
    }
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
  probeCommand = null,
  updaterStatus = null,
} = {}) {
  const packaged = Boolean(app && app.isPackaged);
  const stamp = readInstallStamp({ desktopRoot, resourcesPath });
  const backendManifest = readBackendManifest({ desktopRoot, resourcesPath });
  const appVersion = _safeAppCall(app, "getVersion", "");
  const updateEligibility = releaseUpdateEligibility({ packaged, stamp, backendManifest, platform, appVersion });
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
  const configuredBackend = Boolean(env.AEGIS_HOME || env.AEGIS_BIN);
  const commandResolution = resolveAegisCommand({
    platform,
    env,
    packaged,
    resourcesPath,
    appPath,
    exists,
    ...(probeCommand ? { probeCommand } : {}),
  });
  const pathBackend = commandResolution.usable && commandResolution.source === "path";
  const packagedBackend = commandResolution.usable
    && packagedBackendCandidates.includes(commandResolution.command);
  const backendConfigured = Boolean(commandResolution.usable);
  const backendDetail = backendConfigured
    ? (
      packagedBackend
        ? "packaged backend candidate passed version probe"
        : (
          pathBackend
            ? "AEGIS backend resolved from PATH"
            : (
              configuredBackend
                ? "configured AEGIS backend passed version probe"
                : "AEGIS backend resolved from installed candidate"
            )
        )
    )
    : (
      bundledBackend
        ? `packaged backend candidate exists but did not pass version probe: ${commandResolution.reason}`
        : `packaged desktop cannot find a usable AEGIS backend: ${commandResolution.reason}${
          backendManifest.found && backendManifest.payload && backendManifest.payload.reason
            ? ` (${backendManifest.payload.reason})`
            : ""
        }`
    );
  const remoteDisplayReason = detectRemoteDisplay({ env, platform });
  const updater = updaterStatus && typeof updaterStatus === "object" ? updaterStatus : {};
  const updateStage = String(updater.stage || "");
  const updateChecking = Boolean(updater.checking);
  const updateInstallable = Boolean(updater.installable);
  const updateInstallReason = updateEligibility.ok
    ? (
      updateInstallable
        ? ""
        : (updateStage ? `no downloaded update is ready (stage: ${updateStage})` : "no downloaded update is ready")
    )
    : updateEligibility.reason;
  const updateCheckReason = updateEligibility.ok
    ? (updateChecking ? "an update check is already running" : "")
    : updateEligibility.reason;
  const uninstallPlan = desktopUninstallPlan({ desktopRoot, resourcesPath, platform, exists });
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
      severity: backendConfigured || !packaged ? "ok" : "error",
      detail: backendDetail,
    },
  ];
  return {
    packaged,
    appVersion,
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
      backendManifest: backendManifest.path,
    },
    installStamp: stamp.payload,
    backendManifest: backendManifest.payload,
    updateEligibility,
    updater: {
      stage: updateStage,
      checking: updateChecking,
      installable: updateInstallable,
      installing: Boolean(updater.installing),
      version: String(updater.version || ""),
    },
    renderer: {
      remoteDisplayReason,
      gpuFallbackRecommended: Boolean(remoteDisplayReason),
    },
    backendDiscovery: {
      configured: backendConfigured,
      bundled: bundledBackend,
      command: commandResolution,
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
        {
          id: "check_updates",
          label: "Check for updates",
          description: "Ask the packaged app to check GitHub Releases for an AEGIS update.",
          disabled: !updateEligibility.ok || updateChecking,
          reason: updateCheckReason,
        },
        {
          id: "install_update",
          label: "Install downloaded update",
          description: "Restart AEGIS and install the already-downloaded update.",
          disabled: !updateEligibility.ok || !updateInstallable,
          reason: updateInstallReason,
        },
        {
          id: "uninstall_app",
          label: "Uninstall AEGIS",
          description: "Run the native uninstall script. Local data is kept unless the script is run with purge.",
          disabled: !uninstallPlan.available,
          reason: uninstallPlan.reason,
        },
      ],
    },
  };
}

module.exports = {
  candidateBackendManifestPaths,
  candidateInstallStampPaths,
  desktopDiagnostics,
  detectRemoteDisplay,
  readBackendManifest,
  readInstallStamp,
  releaseUpdateEligibility,
};
