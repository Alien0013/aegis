const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  BACKEND_MANIFEST_SCHEMA_VERSION,
  backendStagePaths,
  stageBackend,
} = require("./stage-backend.cjs");

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aegis-stage-backend-"));
}

test("writes an explicit no-backend manifest when no source is configured", () => {
  const root = tmpRoot();
  const result = stageBackend({
    desktopRoot: root,
    env: {},
    platform: "linux",
    now: () => new Date("2026-06-18T12:00:00.000Z"),
  });

  assert.equal(result.manifest.schemaVersion, BACKEND_MANIFEST_SCHEMA_VERSION);
  assert.equal(result.manifest.staged, false);
  assert.equal(result.manifest.mode, "none");
  assert.deepEqual(result.manifest.targetPlatforms, ["linux"]);
  assert.match(result.manifest.reason, /AEGIS_DESKTOP_BACKEND_SOURCE/);
  assert.equal(fs.existsSync(path.join(result.backendDir, ".placeholder")), true);
  assert.deepEqual(JSON.parse(fs.readFileSync(result.manifestPath, "utf8")), result.manifest);
});

test("rejects release builds without a staged backend unless explicitly external", () => {
  const root = tmpRoot();

  assert.throws(
    () => stageBackend({
      desktopRoot: root,
      env: { AEGIS_RELEASE: "1" },
      platform: "linux",
    }),
    /AEGIS_DESKTOP_BACKEND_SOURCE or AEGIS_ALLOW_EXTERNAL_DESKTOP_BACKEND=1/,
  );
});

test("release builds can explicitly declare an external backend dependency", () => {
  const root = tmpRoot();
  const result = stageBackend({
    desktopRoot: root,
    env: { AEGIS_RELEASE: "1", AEGIS_ALLOW_EXTERNAL_DESKTOP_BACKEND: "1" },
    platform: "linux",
    now: () => new Date("2026-06-18T12:00:00.000Z"),
  });

  assert.equal(result.manifest.staged, false);
  assert.equal(result.manifest.mode, "external");
  assert.equal(result.manifest.externalBackend, true);
  assert.match(result.manifest.reason, /AEGIS_ALLOW_EXTERNAL_DESKTOP_BACKEND=1/);
  assert.equal(fs.existsSync(path.join(result.backendDir, ".placeholder")), true);
});

test("stages a POSIX backend executable into build/backend/bin/aegis", () => {
  const root = tmpRoot();
  const source = path.join(root, "source-aegis");
  fs.writeFileSync(source, "#!/bin/sh\necho aegis\n", "utf8");

  const result = stageBackend({
    desktopRoot: root,
    env: { AEGIS_DESKTOP_BACKEND_SOURCE: source },
    platform: "linux",
    now: () => new Date("2026-06-18T12:00:00.000Z"),
  });
  const target = path.join(result.backendDir, "bin", "aegis");

  assert.equal(result.manifest.staged, true);
  assert.equal(result.manifest.mode, "file");
  assert.deepEqual(result.manifest.targets, ["bin/aegis"]);
  assert.equal(fs.readFileSync(target, "utf8"), fs.readFileSync(source, "utf8"));
});

test("stages a Windows backend executable into build/backend/Scripts/aegis.exe", () => {
  const root = tmpRoot();
  const source = path.join(root, "backend.exe");
  fs.writeFileSync(source, "binary-ish", "utf8");

  const result = stageBackend({
    desktopRoot: root,
    env: { AEGIS_DESKTOP_BACKEND_SOURCE: source, AEGIS_DESKTOP_TARGETS: "win" },
    platform: "linux",
    now: () => new Date("2026-06-18T12:00:00.000Z"),
  });

  assert.equal(result.manifest.staged, true);
  assert.deepEqual(result.manifest.targets, ["Scripts/aegis.exe"]);
  assert.equal(fs.readFileSync(path.join(result.backendDir, "Scripts", "aegis.exe"), "utf8"), "binary-ish");
});

test("rejects a backend directory without a discoverable aegis command", () => {
  const root = tmpRoot();
  const source = path.join(root, "backend-source");
  fs.mkdirSync(source);
  fs.writeFileSync(path.join(source, "README.txt"), "missing command", "utf8");

  assert.throws(
    () => stageBackend({
      desktopRoot: root,
      env: { AEGIS_DESKTOP_BACKEND_SOURCE: source },
      platform: "linux",
    }),
    /must contain one of/,
  );
});

test("stages a ready backend directory unchanged", () => {
  const root = tmpRoot();
  const source = path.join(root, "backend-source");
  const command = path.join(source, "bin", "aegis");
  fs.mkdirSync(path.dirname(command), { recursive: true });
  fs.writeFileSync(command, "#!/bin/sh\necho aegis\n", "utf8");
  fs.chmodSync(command, 0o644);
  fs.writeFileSync(path.join(source, "support.txt"), "kept", "utf8");

  const result = stageBackend({
    desktopRoot: root,
    env: { AEGIS_DESKTOP_BACKEND_SOURCE: source },
    platform: "linux",
  });

  assert.deepEqual(result.manifest.targets, ["bin/aegis"]);
  assert.equal(fs.readFileSync(path.join(result.backendDir, "support.txt"), "utf8"), "kept");
  const target = path.join(result.backendDir, "bin", "aegis");
  assert.equal(fs.existsSync(target), true);
  assert.notEqual(fs.statSync(target).mode & 0o111, 0);
});

test("backendStagePaths uses the desktop build directory", () => {
  const root = tmpRoot();
  assert.deepEqual(backendStagePaths({ desktopRoot: root }), {
    buildDir: path.join(root, "build"),
    backendDir: path.join(root, "build", "backend"),
    manifestPath: path.join(root, "build", "backend-manifest.json"),
  });
});
