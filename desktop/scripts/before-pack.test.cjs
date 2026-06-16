const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { cleanStaleAppOutDir } = require("./before-pack.cjs");

test("cleans stale appOutDir from interrupted electron-builder run", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-pack-"));
  const appOutDir = path.join(root, "linux-unpacked");
  fs.mkdirSync(path.join(appOutDir, "resources"), { recursive: true });
  fs.writeFileSync(path.join(appOutDir, "LICENSE.electron.txt"), "stale");
  fs.writeFileSync(path.join(appOutDir, "resources", "app.asar"), "partial");

  assert.equal(cleanStaleAppOutDir(appOutDir), true);
  assert.equal(fs.existsSync(appOutDir), false);
});

test("cleanStaleAppOutDir is a no-op for absent or invalid targets", () => {
  assert.equal(cleanStaleAppOutDir(""), false);
  assert.equal(cleanStaleAppOutDir(null), false);
  assert.equal(cleanStaleAppOutDir(path.join(os.tmpdir(), "aegis-missing-pack-dir")), false);
});
