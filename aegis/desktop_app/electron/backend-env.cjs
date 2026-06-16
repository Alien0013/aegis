const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { readWindowsUserEnvVar } = require("./windows-user-env.cjs");

const DEFAULT_PROBE_TIMEOUT_MS = 2500;

function _pathFor(platform) {
  return platform === "win32" ? path.win32 : path;
}

function _pathKey(env, platform) {
  if (platform !== "win32") return "PATH";
  return Object.keys(env).find((key) => key.toLowerCase() === "path") || "Path";
}

function _pathDelimiter(platform) {
  return platform === "win32" ? ";" : ":";
}

function sanePathEntries(platform) {
  if (platform === "darwin") {
    return ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"];
  }
  if (platform === "linux") {
    return ["/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin", "/usr/sbin", "/sbin"];
  }
  return [];
}

function normalizePathEnv(baseEnv = process.env, options = {}) {
  const platform = options.platform || process.platform;
  const env = { ...baseEnv };
  const key = _pathKey(env, platform);
  const delimiter = _pathDelimiter(platform);
  const raw = String(env[key] || "");
  const seen = new Set();
  const merged = [];
  const remember = (entry) => {
    const text = String(entry || "").trim();
    if (!text) return;
    const marker = platform === "win32" ? text.toLowerCase() : text;
    if (seen.has(marker)) return;
    seen.add(marker);
    merged.push(text);
  };
  for (const entry of sanePathEntries(platform)) remember(entry);
  for (const entry of raw.split(delimiter)) remember(entry);
  if (merged.length) env[key] = merged.join(delimiter);
  return env;
}

function _isWsl(options = {}) {
  if (typeof options.isWsl === "boolean") return options.isWsl;
  const env = options.env || process.env;
  return Boolean(env.WSL_DISTRO_NAME || env.WSL_INTEROP || env.WSLENV);
}

function _windowsBinaryOnWsl(candidate, options = {}) {
  const platform = options.platform || process.platform;
  if (platform !== "linux" || !_isWsl(options)) return false;
  const normalized = String(candidate || "").replace(/\\/g, "/").toLowerCase();
  return /^\/mnt\/[a-z]\//.test(normalized) && (
    normalized.endsWith(".exe") || normalized.endsWith(".cmd") || normalized.includes("/windowsapps/")
  );
}

function _probeCommand(command, options = {}) {
  if (_windowsBinaryOnWsl(command, options)) return false;
  if (typeof options.probeCommand === "function") {
    return Boolean(options.probeCommand(command, options));
  }
  if (options.probe === false) return true;
  try {
    execFileSync(command, ["--version"], {
      env: normalizePathEnv(options.env || process.env, options),
      stdio: "ignore",
      timeout: options.probeTimeoutMs || DEFAULT_PROBE_TIMEOUT_MS,
    });
    return true;
  } catch {
    return false;
  }
}

function _firstUsable(candidates, exists, options) {
  for (const candidate of candidates) {
    if (candidate && exists(candidate) && _probeCommand(candidate, options)) return candidate;
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
  const env = normalizePathEnv(options.env || process.env, options);
  const resolvedOptions = { ...options, env };
  const direct = _firstUsable(candidateAegisCommands(resolvedOptions), exists, resolvedOptions);
  if (direct) return direct;
  return "aegis";
}

function backendEnvironment(baseEnv = process.env, options = {}) {
  const platform = options.platform || process.platform;
  const env = normalizePathEnv(baseEnv, { ...options, platform });
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
  normalizePathEnv,
  resolveAegisHome,
  sanePathEntries,
};
