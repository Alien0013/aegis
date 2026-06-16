const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

function findRepoRoot(start) {
  let dir = path.resolve(start);
  while (dir !== path.dirname(dir)) {
    if (
      fs.existsSync(path.join(dir, "desktop", "package.json")) &&
      fs.existsSync(path.join(dir, "aegis", "desktop_app", "package.json"))
    ) {
      return dir;
    }
    dir = path.dirname(dir);
  }
  return "";
}

function walkFiles(root, prefix = "") {
  const base = path.join(root, prefix);
  const files = [];
  for (const name of fs.readdirSync(base).sort()) {
    const rel = path.join(prefix, name);
    const full = path.join(root, rel);
    const stat = fs.statSync(full);
    if (stat.isDirectory()) {
      files.push(...walkFiles(root, rel));
    } else if (stat.isFile()) {
      files.push(rel);
    }
  }
  return files;
}

function desktopManifest(root) {
  const roots = ["electron", "scripts"];
  const files = ["package.json", "package-lock.json", "launch.js", "build/icon.png", "build/icon.ico"];
  for (const dir of roots) files.push(...walkFiles(root, dir));
  return files.sort();
}

const repoRoot = findRepoRoot(__dirname);

test("desktop source and packaged Electron copy do not drift", { skip: repoRoot ? false : "repo root not present" }, () => {
  const sourceRoot = path.join(repoRoot, "desktop");
  const packagedRoot = path.join(repoRoot, "aegis", "desktop_app");
  const sourceFiles = desktopManifest(sourceRoot);
  const packagedFiles = desktopManifest(packagedRoot);

  assert.deepEqual(packagedFiles, sourceFiles);
  for (const rel of sourceFiles) {
    assert.deepEqual(
      fs.readFileSync(path.join(packagedRoot, rel)),
      fs.readFileSync(path.join(sourceRoot, rel)),
      `desktop copy drifted: ${rel}`,
    );
  }
});
