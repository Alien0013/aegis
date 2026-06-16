const fs = require("fs");
const os = require("os");
const path = require("path");
const { readWindowsUserEnvVar } = require("./windows-user-env.cjs");

function _pathFor(platform) {
  return platform === "win32" ? path.win32 : path;
}

function _firstExisting(candidates, exists) {
  for (const candidate of candidates) {
    if (candidate && exists(candidate)) return candidate;
  }
  return "";
}

function _envValue(name, options) {
  const env = options.env || process.env;
  if (env[name]) return env[name];
  const read = options.readUserEnvVar || readWindowsUserEnvVar;
  return read(name, options) || "";
}

function resolveAegisHome(options = {}) {
  const platform = options.platform || process.platform;
  const env = options.env || process.env;
  const homedir = options.homedir || os.homedir();
  const pathMod = _pathFor(platform);
  const explicit = _envValue("AEGIS_HOME", { ...options, env, platform });
  if (explicit) return explicit;
  if (platform === "win32") {
    const local = env.LOCALAPPDATA || env.LocalAppData || pathMod.join(homedir, "AppData", "Local");
    return pathMod.join(local, "aegis");
  }
  return pathMod.join(homedir, ".aegis");
}

function candidateAegisCommands(options = {}) {
  const platform = options.platform || process.platform;
  const env = options.env || process.env;
  const homedir = options.homedir || os.homedir();
  const pathMod = _pathFor(platform);
  const home = resolveAegisHome({ ...options, platform, env, homedir });
  const candidates = [];
  const explicit = _envValue("AEGIS_BIN", { ...options, env, platform });
  if (explicit) candidates.push(explicit);
  if (platform === "win32") {
    candidates.push(
      pathMod.join(home, "venv", "Scripts", "aegis.exe"),
      pathMod.join(home, "venv", "Scripts", "aegis.cmd"),
      pathMod.join(homedir, ".aegis", "venv", "Scripts", "aegis.exe"),
      pathMod.join(homedir, ".aegis", "venv", "Scripts", "aegis.cmd"),
    );
  }
  candidates.push(pathMod.join(home, "venv", "bin", "aegis"));
  if (platform !== "win32") {
    candidates.push(pathMod.join(homedir, ".aegis", "venv", "bin", "aegis"));
  }
  return Array.from(new Set(candidates.filter(Boolean)));
}

function aegisCommand(options = {}) {
  const exists = options.exists || fs.existsSync;
  return _firstExisting(candidateAegisCommands(options), exists) || "aegis";
}

function backendEnvironment(baseEnv = process.env, options = {}) {
  const platform = options.platform || process.platform;
  const env = { ...baseEnv };
  const readOptions = { ...options, env, platform };
  const read = options.readUserEnvVar || readWindowsUserEnvVar;
  if (!env.AEGIS_HOME) {
    const home = read("AEGIS_HOME", readOptions);
    if (home) env.AEGIS_HOME = home;
  }
  if (!env.AEGIS_BIN) {
    const bin = read("AEGIS_BIN", readOptions);
    if (bin) env.AEGIS_BIN = bin;
  }
  if (!env.TERMINAL_CWD) {
    env.TERMINAL_CWD = options.cwd || process.cwd();
  }
  return env;
}

module.exports = {
  aegisCommand,
  backendEnvironment,
  candidateAegisCommands,
  resolveAegisHome,
};
