"use strict";

const fs = require("node:fs");
const path = require("node:path");

const REQUIRED_ROOT_INSTALL_FILES = [
  "package.json",
  "web/node_modules/vite/package.json",
  "desktop/node_modules/electron/package.json",
];

function repoRootFromScript(scriptDir = __dirname) {
  return path.resolve(scriptDir, "..", "..", "..");
}

function missingRootInstallFiles(root, exists = fs.existsSync) {
  return REQUIRED_ROOT_INSTALL_FILES.filter((rel) => {
    try {
      return !exists(path.join(root, rel));
    } catch {
      return true;
    }
  });
}

function rootInstallMessage(root, missing) {
  const body = missing.length ? `missing: ${missing.join(", ")}` : "install is ready";
  return `Run from repo root: cd ${root} && npm install (${body})`;
}

function assertRootInstall(options = {}) {
  const root = path.resolve(options.root || repoRootFromScript(options.scriptDir));
  const missing = missingRootInstallFiles(root, options.exists || fs.existsSync);
  if (missing.length) {
    const error = new Error(rootInstallMessage(root, missing));
    error.code = "AEGIS_ROOT_INSTALL_MISSING";
    error.root = root;
    error.missing = missing;
    throw error;
  }
  return { ok: true, root, required: REQUIRED_ROOT_INSTALL_FILES.slice() };
}

if (require.main === module) {
  try {
    assertRootInstall();
  } catch (error) {
    console.error(error && error.message ? error.message : String(error));
    process.exit(1);
  }
}

module.exports = {
  REQUIRED_ROOT_INSTALL_FILES,
  assertRootInstall,
  missingRootInstallFiles,
  repoRootFromScript,
  rootInstallMessage,
};
