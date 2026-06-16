const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  candidateInstallStampPaths,
  desktopDiagnostics,
  readInstallStamp,
} = require("./desktop-status.cjs");

function fakeApp({ packaged = false, version = "0.1.0", userData = "" } = {}) {
  return {
    isPackaged: packaged,
    getVersion: () => version,
    getPath: (name) => (name === "userData" ? userData : ""),
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
    JSON.stringify({ commit: "abc123", branch: "main" }),
  );

  const report = desktopDiagnostics({
    app: fakeApp({ packaged: true, version: "1.2.3", userData: path.join(root, "user-data") }),
    desktopRoot: root,
    resourcesPath: "",
    env: { AEGIS_HOME: "/tmp/aegis" },
    versions: { electron: "33.0.0", chrome: "123", node: "22" },
    platform: "linux",
    arch: "x64",
  });

  assert.equal(report.packaged, true);
  assert.equal(report.appVersion, "1.2.3");
  assert.equal(report.installStamp.commit, "abc123");
  assert.equal(report.checks.find((row) => row.id === "install_stamp").ok, true);
  assert.equal(report.checks.find((row) => row.id === "backend_environment").ok, true);
  assert(report.repair.actions.some((row) => row.id === "restart_backend"));
});

test("desktop diagnostics warns when packaged build has no backend env or stamp", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-status-"));
  const report = desktopDiagnostics({
    app: fakeApp({ packaged: true }),
    desktopRoot: root,
    resourcesPath: "",
    env: {},
    versions: {},
  });

  assert.equal(report.installStamp, null);
  assert.equal(report.checks.find((row) => row.id === "install_stamp").severity, "warning");
  assert.equal(report.checks.find((row) => row.id === "backend_environment").severity, "warning");
});
