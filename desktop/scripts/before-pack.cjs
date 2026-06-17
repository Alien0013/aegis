"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function contextOutDir(contextOrOutDir) {
  if (typeof contextOrOutDir === "string") return path.resolve(contextOrOutDir);
  if (contextOrOutDir && typeof contextOrOutDir.outDir === "string") {
    return path.resolve(contextOrOutDir.outDir);
  }
  return "";
}

function isRootOrHome(target) {
  const resolved = path.resolve(target);
  return resolved === path.parse(resolved).root || resolved === path.resolve(os.homedir());
}

function ensureSafeAppOutDir(appOutDir, contextOrOutDir) {
  const target = path.resolve(appOutDir);
  const outDir = contextOutDir(contextOrOutDir);
  if (!outDir) throw new Error("builder outDir is required before cleaning appOutDir");
  if (isRootOrHome(target) || isRootOrHome(outDir)) {
    throw new Error("refusing to clean root or home directory");
  }
  const relative = path.relative(outDir, target);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error("refusing to clean appOutDir outside builder outDir");
  }
}

function cleanStaleAppOutDir(appOutDir, contextOrOutDir) {
  if (!appOutDir || typeof appOutDir !== "string") return false;
  ensureSafeAppOutDir(appOutDir, contextOrOutDir);
  if (!fs.existsSync(appOutDir)) return false;
  fs.rmSync(appOutDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  return true;
}

exports.cleanStaleAppOutDir = cleanStaleAppOutDir;

exports.default = async function beforePack(context) {
  const appOutDir = context && context.appOutDir;
  try {
    if (cleanStaleAppOutDir(appOutDir, context)) {
      console.log(`[before-pack] removed stale unpacked dir before staging: ${appOutDir}`);
    }
  } catch (err) {
    console.warn(`[before-pack] could not clean ${appOutDir} (${err.message}); continuing`);
  }
};
