"use strict";

const path = require("node:path");

const INTERNAL_ARG_PREFIXES = [
  "--type=",
  "--user-data-dir=",
  "--field-trial-handle=",
  "--enable-features=",
  "--disable-features=",
  "--disable-gpu-sandbox",
  "--lang=",
  "--inspect",
  "--remote-debugging-port=",
];
const PRESERVED_ENV_KEYS = ["AEGIS_HOME", "ELECTRON_DISABLE_SANDBOX"];
const PRESERVED_ENV_PREFIXES = ["AEGIS_DESKTOP_"];

function unpackedDirName(platform = process.platform) {
  if (platform === "darwin") return "mac-unpacked";
  if (platform === "win32") return "win-unpacked";
  return "linux-unpacked";
}

function resolveUnpackedRelease(execPath, updateRoot, platform = process.platform) {
  if (!execPath || !updateRoot) return null;
  const unpacked = path.join(updateRoot, "apps", "desktop", "release", unpackedDirName(platform));
  const resolvedExec = path.resolve(String(execPath));
  const resolvedUnpacked = path.resolve(unpacked);
  const prefix = resolvedUnpacked.endsWith(path.sep) ? resolvedUnpacked : `${resolvedUnpacked}${path.sep}`;
  return resolvedExec === resolvedUnpacked || resolvedExec.startsWith(prefix) ? resolvedUnpacked : null;
}

function decideRelaunchOutcome({ underUnpacked, sandboxOk, sandboxFallback = false } = {}) {
  if (!underUnpacked) return "guiSkew";
  if (!sandboxOk && !sandboxFallback) return "manual";
  return "relaunch";
}

function sandboxPreflight(unpackedDir, statSync) {
  if (!unpackedDir) return { ok: false, reason: "no-unpacked-dir", path: null };
  const sandboxPath = path.join(unpackedDir, "chrome-sandbox");
  let stat;
  try {
    stat = statSync(sandboxPath);
  } catch {
    return { ok: true, reason: "no-sandbox-helper", path: sandboxPath };
  }
  const ownedByRoot = stat.uid === 0;
  const hasSetuid = (stat.mode & 0o4000) !== 0;
  if (ownedByRoot && hasSetuid) return { ok: true, reason: "launchable", path: sandboxPath };
  if (!ownedByRoot && !hasSetuid) return { ok: false, reason: "not-root-not-setuid", path: sandboxPath };
  if (!ownedByRoot) return { ok: false, reason: "not-root", path: sandboxPath };
  return { ok: false, reason: "not-setuid", path: sandboxPath };
}

function sandboxFallbackFromEnv(env = {}, launchArgs = []) {
  const disabled = String(env.ELECTRON_DISABLE_SANDBOX || "").trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(disabled)) return true;
  return Array.isArray(launchArgs) && launchArgs.includes("--no-sandbox");
}

function collectRelaunchArgs(argv = []) {
  if (!Array.isArray(argv)) return [];
  return argv.filter((arg) => {
    if (typeof arg !== "string" || !arg) return false;
    return !INTERNAL_ARG_PREFIXES.some((prefix) => (
      prefix.endsWith("=") ? arg.startsWith(prefix) : arg === prefix || arg.startsWith(`${prefix}=`)
    ));
  });
}

function collectRelaunchEnv(env = {}) {
  const out = {};
  if (!env || typeof env !== "object") return out;
  for (const [key, value] of Object.entries(env)) {
    if (value == null) continue;
    if (PRESERVED_ENV_KEYS.includes(key) || PRESERVED_ENV_PREFIXES.some((prefix) => key.startsWith(prefix))) {
      out[key] = String(value);
    }
  }
  return out;
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

function buildRelaunchScript({ pid, execPath, args = [], env = {}, cwd = "" } = {}) {
  const exports = Object.entries(env).map(([key, value]) => `export ${key}=${shellQuote(value)}`).join("\n");
  const quotedArgs = args.map(shellQuote).join(" ");
  const cwdLine = cwd ? `cd ${shellQuote(cwd)} 2>/dev/null || true` : "";
  return [
    "#!/bin/bash",
    "set -u",
    `APP_PID=${Number(pid)}`,
    "for _ in $(seq 1 60); do",
    "  kill -0 \"$APP_PID\" 2>/dev/null || break",
    "  sleep 0.5",
    "done",
    "if kill -0 \"$APP_PID\" 2>/dev/null; then",
    "  kill -9 \"$APP_PID\" 2>/dev/null || true",
    "  sleep 0.5",
    "fi",
    "rm -f -- \"$0\" 2>/dev/null || true",
    cwdLine,
    exports,
    `exec ${shellQuote(execPath)}${quotedArgs ? ` ${quotedArgs}` : ""}`,
    "",
  ].join("\n");
}

module.exports = {
  buildRelaunchScript,
  collectRelaunchArgs,
  collectRelaunchEnv,
  decideRelaunchOutcome,
  resolveUnpackedRelease,
  sandboxFallbackFromEnv,
  sandboxPreflight,
  unpackedDirName,
};
