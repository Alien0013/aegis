const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  candidateInstallStampPaths,
  desktopDiagnostics,
  detectRemoteDisplay,
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
      targetPlatforms: ["linux"],
    }),
  );

  const report = desktopDiagnostics({
    app: fakeApp({ packaged: true, version: "1.2.3", userData: path.join(root, "user-data") }),
    desktopRoot: root,
    resourcesPath: "",
    env: { AEGIS_HOME: "/tmp/aegis" },
    versions: { electron: "33.0.0", chrome: "123", node: "22" },
    platform: "linux",
    arch: "x64",
    probeCommand: () => true,
  });

  assert.equal(report.packaged, true);
  assert.equal(report.appVersion, "1.2.3");
  assert.equal(report.installStamp.commit, "abc123");
  assert.equal(report.updateEligibility.ok, true);
  assert.equal(report.renderer.gpuFallbackRecommended, false);
  assert.equal(report.backendDiscovery.configured, true);
  assert.equal(report.backendDiscovery.bundled, false);
  assert.equal(report.checks.find((row) => row.id === "install_stamp").ok, true);
  assert.equal(report.checks.find((row) => row.id === "release_update_eligibility").ok, true);
  assert.equal(report.checks.find((row) => row.id === "backend_environment").ok, true);
  assert(report.repair.actions.some((row) => row.id === "restart_backend"));
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
      stamp: {
        found: true,
        payload: { schemaVersion: 2, release: true, trustedRelease: true, dirty: false, targetPlatforms: ["linux"] },
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
});
