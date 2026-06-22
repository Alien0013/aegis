const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const beforeBuild = require("./before-build.cjs");
const {
  STAMP_SCHEMA_VERSION,
  inferTargetPlatforms,
  releaseBuildFailures,
  writeBuildStamp,
} = require("./write-build-stamp.cjs");

function packageJson(overrides = {}) {
  return {
    name: "aegis-desktop",
    productName: "AEGIS",
    version: "9.8.7",
    devDependencies: { electron: "^33.0.0" },
    build: {
      electronVersion: "33.4.11",
      publish: [{ provider: "github", owner: "Alien0013", repo: "aegis" }],
      win: { signAndEditExecutable: false },
      ...(overrides.build || {}),
    },
    ...overrides,
  };
}

test("writes install stamp from CI-style environment", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-stamp-"));
  const commit = "a".repeat(40);

  const result = writeBuildStamp({
    desktopRoot: root,
    repoRoot: root,
    env: { GITHUB_SHA: commit, GITHUB_REF_NAME: "main", CI: "true" },
    now: () => new Date("2026-06-16T12:00:00.000Z"),
    packageJson: packageJson(),
    platform: "linux",
    arch: "x64",
    versions: { node: "22.11.0" },
  });

  assert.equal(result.path, path.join(root, "build", "install-stamp.json"));
  const payload = JSON.parse(fs.readFileSync(result.path, "utf8"));
  assert.equal(payload.schemaVersion, STAMP_SCHEMA_VERSION);
  assert.equal(payload.appName, "AEGIS");
  assert.equal(payload.appVersion, "9.8.7");
  assert.equal(payload.electronVersion, "33.4.11");
  assert.equal(payload.nodeVersion, "22.11.0");
  assert.equal(payload.platform, "linux");
  assert.equal(payload.arch, "x64");
  assert.deepEqual(payload.targetPlatforms, ["linux"]);
  assert.equal(payload.release, false);
  assert.equal(payload.trustedRelease, false);
  assert.equal(payload.commit, commit);
  assert.equal(payload.branch, "main");
  assert.equal(payload.builtAt, "2026-06-16T12:00:00.000Z");
  assert.equal(payload.dirty, false);
  assert.equal(payload.source, "ci");
  assert.equal(payload.ci, true);
});

test("writes non-release fallback stamp outside git checkouts", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-stamp-"));

  const result = writeBuildStamp({
    desktopRoot: root,
    repoRoot: root,
    env: {},
    now: () => new Date("2026-06-16T12:30:00.000Z"),
    packageJson: packageJson(),
    platform: "linux",
    arch: "x64",
    versions: { node: "22.11.0" },
  });

  const payload = JSON.parse(fs.readFileSync(result.path, "utf8"));
  assert.equal(payload.commit, "unknown-local");
  assert.equal(payload.source, "local-fallback");
  assert.equal(payload.release, false);
  assert.equal(payload.trustedRelease, false);
  assert.equal(payload.dirty, false);
});

test("infers electron-builder targets from npm lifecycle", () => {
  assert.deepEqual(inferTargetPlatforms({ env: { npm_lifecycle_event: "dist:win" }, platform: "linux" }), ["win32"]);
  assert.deepEqual(inferTargetPlatforms({ env: { AEGIS_DESKTOP_TARGETS: "win,mac,linux" }, platform: "linux" }), [
    "win32",
    "darwin",
    "linux",
  ]);
});

test("release guard refuses unsigned Windows release builds", () => {
  const failures = releaseBuildFailures({
    env: {
      AEGIS_RELEASE: "1",
      GITHUB_SHA: "b".repeat(40),
      GH_TOKEN: "token",
    },
    packageJson: packageJson(),
    stamp: { source: "ci", dirty: false },
    targetPlatforms: ["win32"],
  });
  assert(failures.some((message) => message.includes("signAndEditExecutable=false")));
});

test("release guard allows explicit unsigned Windows override", () => {
  const failures = releaseBuildFailures({
    env: {
      AEGIS_RELEASE: "1",
      GITHUB_SHA: "b".repeat(40),
      GH_TOKEN: "token",
      AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE: "1",
    },
    packageJson: packageJson(),
    stamp: { source: "ci", dirty: false },
    targetPlatforms: ["win32"],
  });
  assert.deepEqual(failures, []);
});

test("release guard requires mac signing and notarization unless explicitly unsigned", () => {
  const base = {
    AEGIS_RELEASE: "1",
    GITHUB_SHA: "b".repeat(40),
    GH_TOKEN: "token",
  };
  const missing = releaseBuildFailures({
    env: base,
    packageJson: packageJson(),
    stamp: { source: "ci", dirty: false },
    targetPlatforms: ["darwin"],
  });
  assert(missing.some((message) => message.includes("CSC_LINK/CSC_NAME")));
  assert(missing.some((message) => message.includes("Apple notarization credentials")));

  const signed = releaseBuildFailures({
    env: {
      ...base,
      CSC_LINK: "base64-p12",
      CSC_KEY_PASSWORD: "password",
      APPLE_ID: "dev@example.com",
      APPLE_APP_SPECIFIC_PASSWORD: "app-pass",
      APPLE_TEAM_ID: "TEAM123",
    },
    packageJson: packageJson(),
    stamp: { source: "ci", dirty: false },
    targetPlatforms: ["darwin"],
  });
  assert.deepEqual(signed, []);

  const unsigned = releaseBuildFailures({
    env: { ...base, AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE: "1" },
    packageJson: packageJson(),
    stamp: { source: "ci", dirty: false },
    targetPlatforms: ["darwin"],
  });
  assert.deepEqual(unsigned, []);
});

test("release guard allows stamped Linux GitHub release builds", () => {
  const failures = releaseBuildFailures({
    env: {
      AEGIS_RELEASE: "1",
      GITHUB_SHA: "d".repeat(40),
      GITHUB_TOKEN: "token",
      npm_lifecycle_event: "dist:linux",
    },
    packageJson: packageJson(),
    stamp: { source: "ci", dirty: false },
    targetPlatforms: ["linux"],
  });
  assert.deepEqual(failures, []);
});

test("writeBuildStamp fails unsafe release builds before writing", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-stamp-"));
  assert.throws(
    () => writeBuildStamp({
      desktopRoot: root,
      repoRoot: root,
      env: {
        AEGIS_RELEASE: "1",
        GITHUB_SHA: "c".repeat(40),
        GITHUB_REF_NAME: "main",
        npm_lifecycle_event: "dist:win",
        GH_TOKEN: "token",
      },
      packageJson: packageJson(),
    }),
    /unsafe desktop release build/,
  );
  assert.equal(fs.existsSync(path.join(root, "build", "install-stamp.json")), false);
});

test("before-build hook is callable by electron-builder", async () => {
  assert.equal(typeof beforeBuild, "function");
});
