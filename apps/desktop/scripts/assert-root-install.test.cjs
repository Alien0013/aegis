"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const {
  REQUIRED_ROOT_INSTALL_FILES,
  assertRootInstall,
  missingRootInstallFiles,
  repoRootFromScript,
  rootInstallMessage,
} = require("./assert-root-install.cjs");

test("repoRootFromScript resolves from apps/desktop/scripts to repo root", () => {
  assert.equal(
    repoRootFromScript(path.join("/repo", "apps", "desktop", "scripts")),
    path.resolve("/repo"),
  );
});

test("missingRootInstallFiles reports AEGIS workspace dependency sentinels", () => {
  const seen = [];
  const missing = missingRootInstallFiles("/repo", (candidate) => {
    seen.push(candidate.replace(/\\/g, "/"));
    return !candidate.includes("web/node_modules/vite/package.json");
  });
  assert.deepEqual(missing, ["web/node_modules/vite/package.json"]);
  assert.equal(seen.some((item) => item.endsWith("desktop/node_modules/electron/package.json")), true);
});

test("assertRootInstall returns required files when all sentinels exist", () => {
  const result = assertRootInstall({ root: "/repo", exists: () => true });
  assert.equal(result.ok, true);
  assert.deepEqual(result.required, REQUIRED_ROOT_INSTALL_FILES);
});

test("assertRootInstall throws actionable command when install is incomplete", () => {
  assert.throws(
    () => assertRootInstall({ root: "/repo", exists: () => false }),
    (error) => {
      assert.equal(error.code, "AEGIS_ROOT_INSTALL_MISSING");
      assert.equal(error.root, path.resolve("/repo"));
      assert.equal(error.missing.includes("web/node_modules/vite/package.json"), true);
      assert.match(error.message, /cd \/repo && npm install/);
      return true;
    },
  );
});

test("rootInstallMessage names the missing files", () => {
  assert.match(
    rootInstallMessage("/repo", ["web/node_modules/vite/package.json"]),
    /missing: web\/node_modules\/vite\/package.json/,
  );
});
