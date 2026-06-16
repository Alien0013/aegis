const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const beforeBuild = require("./before-build.cjs");
const { STAMP_SCHEMA_VERSION, writeBuildStamp } = require("./write-build-stamp.cjs");

test("writes install stamp from CI-style environment", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-stamp-"));
  const commit = "a".repeat(40);

  const result = writeBuildStamp({
    desktopRoot: root,
    repoRoot: root,
    env: { GITHUB_SHA: commit, GITHUB_REF_NAME: "main" },
    now: () => new Date("2026-06-16T12:00:00.000Z"),
  });

  assert.equal(result.path, path.join(root, "build", "install-stamp.json"));
  const payload = JSON.parse(fs.readFileSync(result.path, "utf8"));
  assert.equal(payload.schemaVersion, STAMP_SCHEMA_VERSION);
  assert.equal(payload.commit, commit);
  assert.equal(payload.branch, "main");
  assert.equal(payload.builtAt, "2026-06-16T12:00:00.000Z");
  assert.equal(payload.dirty, false);
  assert.equal(payload.source, "ci");
});

test("before-build hook is callable by electron-builder", async () => {
  assert.equal(typeof beforeBuild, "function");
});
