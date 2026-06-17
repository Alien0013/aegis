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

  assert.equal(cleanStaleAppOutDir(appOutDir, { outDir: root }), true);
  assert.equal(fs.existsSync(appOutDir), false);
});

test("cleanStaleAppOutDir is a no-op for absent or invalid targets", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-pack-"));
  assert.equal(cleanStaleAppOutDir(""), false);
  assert.equal(cleanStaleAppOutDir(null), false);
  assert.equal(cleanStaleAppOutDir(path.join(root, "missing-unpacked"), { outDir: root }), false);
});

test("cleanStaleAppOutDir refuses targets outside builder output", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-pack-"));
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-pack-outside-"));
  const stale = path.join(outside, "stale.txt");
  fs.writeFileSync(stale, "keep");

  assert.throws(
    () => cleanStaleAppOutDir(outside, { outDir: root }),
    /outside builder outDir/,
  );
  assert.equal(fs.readFileSync(stale, "utf8"), "keep");
});

test("cleanStaleAppOutDir refuses root, home, and builder output roots", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-pack-"));
  const appOutDir = path.join(root, "linux-unpacked");
  fs.mkdirSync(appOutDir, { recursive: true });

  assert.throws(() => cleanStaleAppOutDir(path.parse(root).root, { outDir: root }), /root or home/);
  assert.throws(() => cleanStaleAppOutDir(os.homedir(), { outDir: root }), /root or home/);
  assert.throws(() => cleanStaleAppOutDir(root, { outDir: root }), /outside builder outDir/);
  assert.throws(() => cleanStaleAppOutDir(appOutDir), /outDir is required/);
});
