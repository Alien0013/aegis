"use strict";

const fs = require("node:fs");

function cleanStaleAppOutDir(appOutDir) {
  if (!appOutDir || typeof appOutDir !== "string") return false;
  if (!fs.existsSync(appOutDir)) return false;
  fs.rmSync(appOutDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  return true;
}

exports.cleanStaleAppOutDir = cleanStaleAppOutDir;

exports.default = async function beforePack(context) {
  const appOutDir = context && context.appOutDir;
  try {
    if (cleanStaleAppOutDir(appOutDir)) {
      console.log(`[before-pack] removed stale unpacked dir before staging: ${appOutDir}`);
    }
  } catch (err) {
    console.warn(`[before-pack] could not clean ${appOutDir} (${err.message}); continuing`);
  }
};
