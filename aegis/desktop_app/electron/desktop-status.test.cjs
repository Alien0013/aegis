const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  candidateBackendManifestPaths,
  candidateInstallStampPaths,
  desktopDiagnostics,
  detectRemoteDisplay,
  readBackendManifest,
  readInstallStamp,
  releaseUpdateEligibility,
} = require("./desktop-status.cjs");

function fakeApp({ packaged = false, version = "0.1.0", userData = "", appPath = "" } = {}) {
  return {
    isPackaged: packaged,
    getVersion: () => version,
    getPath: (name) => (name === "userData" ? userData : ""),
    getAppPath: () => appPath,
  };
}

function writeStagedBackendManifest(root) {
  fs.writeFileSync(
    path.join(root, "build", "backend-manifest.json"),
    JSON.stringify({
      schemaVersion: 1,
      staged: true,
      mode: "file",
      targets: ["bin/aegis"],
    }),
  );
}

test("discovers install stamp from packaged resources before dev build dir", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-status-"));
  const resources = path.join(root, "resources");
  fs.mkdirSync(resources, { recursive: true });
  fs.mkdirSync(path.join(root, "build"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "build", "install-stamp.json"),
    JSON.stringify({ commit: "dev" }),
  );
  fs.writeFileSync(
    path.join(resources, "install-stamp.json"),
    JSON.stringify({ commit: "packaged" }),
  );

  assert.deepEqual(candidateInstallStampPaths({ desktopRoot: root, resourcesPath: resources }), [
    path.join(resources, "install-stamp.json"),
    path.join(root, "build", "install-stamp.json"),
  ]);
  const stamp = readInstallStamp({ desktopRoot: root, resourcesPath: resources });
  assert.equal(stamp.found, true);
  assert.equal(stamp.payload.commit, "packaged");
});

test("discovers backend manifest from packaged resources before dev build dir", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-status-"));
  const resources = path.join(root, "resources");
  fs.mkdirSync(resources, { recursive: true });
  fs.mkdirSync(path.join(root, "build"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "build", "backend-manifest.json"),
    JSON.stringify({ staged: false, source: "dev" }),
  );
  fs.writeFileSync(
    path.join(resources, "backend-manifest.json"),
    JSON.stringify({ staged: true, source: "packaged" }),
  );

  assert.deepEqual(candidateBackendManifestPaths({ desktopRoot: root, resourcesPath: resources }), [
    path.join(resources, "backend-manifest.json"),
    path.join(root, "build", "backend-manifest.json"),
  ]);
  const manifest = readBackendManifest({ desktopRoot: root, resourcesPath: resources });
  assert.equal(manifest.found, true);
  assert.equal(manifest.payload.source, "packaged");
});

test("desktop diagnostics exposes runtime, stamp, checks, and repair actions", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-status-"));
  fs.mkdirSync(path.join(root, "build"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "build", "install-stamp.json"),
    JSON.stringify({
      schemaVersion: 2,
      commit: "abc123",
      branch: "main",
      release: true,
      trustedRelease: true,
      dirty: false,
      appVersion: "1.2.3",
      targetPlatforms: ["linux"],
    }),
  );
  writeStagedBackendManifest(root);

  const report = desktopDiagnostics({
    app: fakeApp({ packaged: true, version: "1.2.3", userData: path.join(root, "user-data") }),
    desktopRoot: root,
    resourcesPath: "",
    env: { AEGIS_HOME: "/tmp/aegis" },
    versions: { electron: "33.0.0", chrome: "123", node: "22" },
    platform: "linux",
    arch: "x64",
    probeCommand: () => true,
    updaterStatus: { stage: "idle", checking: false, installable: false, version: "" },
  });

  assert.equal(report.packaged, true);
  assert.equal(report.appVersion, "1.2.3");
  assert.equal(report.installStamp.commit, "abc123");
  assert.equal(report.updateEligibility.ok, true);
  assert.equal(report.renderer.gpuFallbackRecommended, false);
  assert.equal(report.backendDiscovery.configured, true);
  assert.equal(report.backendDiscovery.bundled, false);
  assert.equal(report.backendManifest.staged, true);
  assert.equal(report.updater.stage, "idle");
  assert.equal(report.updater.installable, false);
  assert.equal(report.paths.backendManifest, path.join(root, "build", "backend-manifest.json"));
  assert.equal(report.checks.find((row) => row.id === "install_stamp").ok, true);
  assert.equal(report.checks.find((row) => row.id === "release_update_eligibility").ok, true);
  assert.equal(report.checks.find((row) => row.id === "backend_environment").ok, true);
  assert(report.repair.actions.some((row) => row.id === "restart_backend"));
  const checkUpdates = report.repair.actions.find((row) => row.id === "check_updates");
  const installUpdate = report.repair.actions.find((row) => row.id === "install_update");
  assert.equal(checkUpdates.disabled, false);
  assert.equal(checkUpdates.reason, "");
  assert.equal(installUpdate.disabled, true);
  assert.match(installUpdate.reason, /no downloaded update is ready/);
});

test("desktop diagnostics gates update repair actions on live updater state", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-status-"));
  fs.mkdirSync(path.join(root, "build"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "build", "install-stamp.json"),
    JSON.stringify({
      schemaVersion: 2,
      release: true,
      trustedRelease: true,
      dirty: false,
      appVersion: "1.2.3",
      targetPlatforms: ["linux"],
    }),
  );
  writeStagedBackendManifest(root);

  const checking = desktopDiagnostics({
    app: fakeApp({ packaged: true, version: "1.2.3" }),
    desktopRoot: root,
    env: { AEGIS_BIN: "/bin/aegis" },
    platform: "linux",
    probeCommand: () => true,
    updaterStatus: { stage: "checking", checking: true, installable: false },
  });
  const checkingAction = checking.repair.actions.find((row) => row.id === "check_updates");
  const checkingInstall = checking.repair.actions.find((row) => row.id === "install_update");
  assert.equal(checking.updater.stage, "checking");
  assert.equal(checkingAction.disabled, true);
  assert.match(checkingAction.reason, /already running/);
  assert.equal(checkingInstall.disabled, true);
  assert.match(checkingInstall.reason, /stage: checking/);

  const ready = desktopDiagnostics({
    app: fakeApp({ packaged: true, version: "1.2.3" }),
    desktopRoot: root,
    env: { AEGIS_BIN: "/bin/aegis" },
    platform: "linux",
    probeCommand: () => true,
    updaterStatus: { stage: "ready", checking: false, installable: true, version: "1.2.4" },
  });
  const readyCheck = ready.repair.actions.find((row) => row.id === "check_updates");
  const readyInstall = ready.repair.actions.find((row) => row.id === "install_update");
  assert.equal(ready.updater.version, "1.2.4");
  assert.equal(readyCheck.disabled, false);
  assert.equal(readyInstall.disabled, false);
});

test("desktop diagnostics treats a packaged resource backend as configured", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-status-"));
  const resources = path.join(root, "resources");
  const bundled = path.join(resources, "aegis", "bin", "aegis");
  fs.mkdirSync(path.dirname(bundled), { recursive: true });
  fs.writeFileSync(bundled, "echo aegis\n");

  const report = desktopDiagnostics({
    app: fakeApp({ packaged: true, appPath: path.join(resources, "app.asar") }),
    desktopRoot: root,
    resourcesPath: resources,
    env: {},
    platform: "linux",
    versions: {},
    probeCommand: (command) => command === bundled,
  });

  assert.equal(report.backendDiscovery.configured, true);
  assert.equal(report.backendDiscovery.bundled, true);
  assert(report.backendDiscovery.packagedCandidates.includes(bundled));
  assert(report.backendDiscovery.packagedPathEntries.includes(path.dirname(bundled)));
  assert.equal(report.checks.find((row) => row.id === "backend_environment").severity, "ok");
  assert.equal(report.backendDiscovery.command.command, bundled);
  assert.equal(report.backendDiscovery.command.usable, true);
});

test("desktop diagnostics rejects an unprobeable packaged resource backend", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-status-"));
  const resources = path.join(root, "resources");
  const bundled = path.join(resources, "aegis", "bin", "aegis");
  fs.mkdirSync(path.dirname(bundled), { recursive: true });
  fs.writeFileSync(bundled, "not a valid backend\n");

  const report = desktopDiagnostics({
    app: fakeApp({ packaged: true, appPath: path.join(resources, "app.asar") }),
    desktopRoot: root,
    resourcesPath: resources,
    env: {},
    platform: "linux",
    versions: {},
    probeCommand: () => false,
  });

  const check = report.checks.find((row) => row.id === "backend_environment");
  assert.equal(report.backendDiscovery.bundled, true);
  assert.equal(report.backendDiscovery.configured, false);
  assert.equal(report.backendDiscovery.command.usable, false);
  assert.equal(check.ok, false);
  assert.equal(check.severity, "error");
  assert.match(check.detail, /candidate exists but did not pass version probe/);
});

test("desktop diagnostics reports packaged backend bootstrap failure as error", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-status-"));
  const resources = path.join(root, "resources");

  const report = desktopDiagnostics({
    app: fakeApp({ packaged: true, appPath: path.join(resources, "app.asar") }),
    desktopRoot: root,
    resourcesPath: resources,
    env: {},
    platform: "linux",
    versions: {},
    exists: () => false,
    probeCommand: () => false,
  });

  const check = report.checks.find((row) => row.id === "backend_environment");
  assert.equal(report.backendDiscovery.configured, false);
  assert.equal(report.backendDiscovery.bundled, false);
  assert.equal(report.backendDiscovery.command.command, "aegis");
  assert.equal(report.backendDiscovery.command.usable, false);
  assert.equal(check.ok, false);
  assert.equal(check.severity, "error");
  assert.match(check.detail, /cannot find a usable AEGIS backend/);
});

test("releaseUpdateEligibility rejects dev, dirty, stale, and mismatched install stamps", () => {
  assert.equal(releaseUpdateEligibility({ packaged: false }).ok, false);
  assert.match(
    releaseUpdateEligibility({ packaged: true, stamp: { found: false, error: "missing" } }).reason,
    /missing/,
  );
  assert.match(
    releaseUpdateEligibility({
      packaged: true,
      stamp: { found: true, payload: { schemaVersion: 1, release: true, trustedRelease: true } },
    }).reason,
    /too old/,
  );
  assert.match(
    releaseUpdateEligibility({
      packaged: true,
      stamp: { found: true, payload: { schemaVersion: 2, release: false, trustedRelease: false } },
    }).reason,
    /trusted release/,
  );
  assert.match(
    releaseUpdateEligibility({
      packaged: true,
      stamp: {
        found: true,
        payload: { schemaVersion: 2, release: true, trustedRelease: true, dirty: true },
      },
    }).reason,
    /dirty/,
  );
  assert.match(
    releaseUpdateEligibility({
      packaged: true,
      appVersion: "1.2.3",
      stamp: {
        found: true,
        payload: {
          schemaVersion: 2,
          release: true,
          trustedRelease: true,
          dirty: false,
          appVersion: "9.9.9",
          targetPlatforms: ["linux"],
        },
      },
    }).reason,
    /does not match running app 1\.2\.3/,
  );
  assert.match(
    releaseUpdateEligibility({
      packaged: true,
      platform: "linux",
      stamp: {
        found: true,
        payload: { schemaVersion: 2, release: true, trustedRelease: true, dirty: false, targetPlatforms: ["win32"] },
      },
    }).reason,
    /does not match linux/,
  );
  assert.equal(
    releaseUpdateEligibility({
      packaged: true,
      platform: "linux",
      appVersion: "1.2.3",
      stamp: {
        found: true,
        payload: {
          schemaVersion: 2,
          release: true,
          trustedRelease: true,
          dirty: false,
          appVersion: "1.2.3",
          targetPlatforms: ["linux"],
        },
      },
    }).ok,
    true,
  );
  assert.match(
    releaseUpdateEligibility({
      packaged: true,
      stamp: {
        found: true,
        payload: {
          schemaVersion: 2,
          release: true,
          trustedRelease: true,
          dirty: false,
          targetPlatforms: ["linux"],
        },
      },
      backendManifest: { found: false, error: "backend manifest missing" },
    }).reason,
    /backend manifest missing/,
  );
  assert.match(
    releaseUpdateEligibility({
      packaged: true,
      stamp: {
        found: true,
        payload: {
          schemaVersion: 2,
          release: true,
          trustedRelease: true,
          dirty: false,
          targetPlatforms: ["linux"],
        },
      },
      backendManifest: {
        found: true,
        payload: { schemaVersion: 1, staged: false, mode: "external", externalBackend: true },
      },
    }).reason,
    /external-backend desktop releases are not auto-update eligible/,
  );
  assert.equal(
    releaseUpdateEligibility({
      packaged: true,
      stamp: {
        found: true,
        payload: {
          schemaVersion: 2,
          release: true,
          trustedRelease: true,
          dirty: false,
          targetPlatforms: ["linux"],
        },
      },
      backendManifest: {
        found: true,
        payload: { schemaVersion: 1, staged: true, mode: "file", targets: ["bin/aegis"] },
      },
    }).ok,
    true,
  );
});

test("detects remote renderer sessions and honors override", () => {
  assert.equal(detectRemoteDisplay({ env: { DISPLAY: ":0" }, platform: "linux" }), null);
  assert.match(String(detectRemoteDisplay({ env: { DISPLAY: "localhost:10.0" }, platform: "linux" })), /x11-forwarding/);
  assert.match(String(detectRemoteDisplay({ env: { SESSIONNAME: "RDP-Tcp#7" }, platform: "win32" })), /^rdp/);
  assert.equal(
    detectRemoteDisplay({ env: { SSH_CONNECTION: "1.2.3.4 5 6.7.8.9 22" }, platform: "darwin" }),
    "ssh-session",
  );
  assert.match(
    String(detectRemoteDisplay({ env: { AEGIS_DESKTOP_DISABLE_GPU: "yes", DISPLAY: ":0" }, platform: "linux" })),
    /override/,
  );
  assert.equal(
    detectRemoteDisplay({
      env: { AEGIS_DESKTOP_DISABLE_GPU: "0", SSH_CONNECTION: "1.2.3.4 5 6.7.8.9 22" },
      platform: "linux",
    }),
    null,
  );
});

test("desktop diagnostics errors when packaged build has no backend env or stamp", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-status-"));
  const report = desktopDiagnostics({
    app: fakeApp({ packaged: true }),
    desktopRoot: root,
    resourcesPath: "",
    env: {},
    versions: {},
    exists: () => false,
    probeCommand: () => false,
  });

  assert.equal(report.installStamp, null);
  assert.equal(report.checks.find((row) => row.id === "install_stamp").severity, "warning");
  assert.equal(report.checks.find((row) => row.id === "release_update_eligibility").severity, "warning");
  assert.equal(report.checks.find((row) => row.id === "backend_environment").severity, "error");
  const checkUpdates = report.repair.actions.find((row) => row.id === "check_updates");
  const installUpdate = report.repair.actions.find((row) => row.id === "install_update");
  assert.equal(checkUpdates.disabled, true);
  assert.match(checkUpdates.reason, /install stamp/);
  assert.equal(installUpdate.disabled, true);
  assert.match(installUpdate.reason, /install stamp/);
});
