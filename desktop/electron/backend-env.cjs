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

function _commandNames(platform) {
  if (platform === "win32") return ["aegis.exe", "aegis.cmd", "aegis.bat"];
  return ["aegis"];
}

function sanePathEntries(platform) {
  if (platform === "darwin") {
    return ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"];
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
  for (const entry of options.prependPathEntries || []) remember(entry);
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
    normalized.endsWith(".exe") ||
    normalized.endsWith(".cmd") ||
    normalized.endsWith(".bat") ||
    normalized.endsWith(".ps1") ||
    normalized.includes("/windowsapps/")
  );
}

function hiddenWindowsChildOptions(childOptions = {}, options = {}) {
  const platform = options.platform || process.platform;
  if (platform !== "win32" || Object.prototype.hasOwnProperty.call(childOptions, "windowsHide")) {
    return childOptions;
  }
  return { ...childOptions, windowsHide: true };
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
      ...hiddenWindowsChildOptions({}, options),
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

function _userEnvValue(name, options) {
  const platform = options.platform || process.platform;
  if (platform !== "win32") return "";
  const read = options.readUserEnvVar || readWindowsUserEnvVar;
  return read(name, options) || "";
}

function _withWindowsUserPath(baseEnv, options = {}) {
  const platform = options.platform || process.platform;
  if (platform !== "win32") return { ...baseEnv };
  const env = { ...baseEnv };
  const read = options.readUserEnvVar || readWindowsUserEnvVar;
  const userPath = read("Path", { ...options, env, platform }) || read("PATH", { ...options, env, platform });
  if (!userPath) return env;
  const key = _pathKey(env, platform);
  env[key] = [env[key], userPath].filter(Boolean).join(_pathDelimiter(platform));
  return env;
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

function desktopLogPath(options = {}) {
  const platform = options.platform || process.platform;
  const pathMod = _pathFor(platform);
  const home = resolveAegisHome(options);
  if (home) return pathMod.join(home, "logs", "desktop.log");
  return pathMod.join(options.userData || os.homedir(), "desktop.log");
}

function _packagedResourceRoots(options = {}) {
  const pathMod = _pathFor(options.platform || process.platform);
  const roots = [];
  const remember = (value) => {
    const text = String(value || "").trim();
    if (text && !roots.includes(text)) roots.push(text);
  };
  const resourcesPath = options.resourcesPath || "";
  const appPath = options.appPath || "";
  remember(resourcesPath);
  if (resourcesPath) remember(pathMod.join(resourcesPath, "app.asar.unpacked"));
  if (appPath) {
    remember(appPath);
    remember(pathMod.dirname(appPath));
    if (pathMod.basename(appPath) === "app.asar") {
      remember(`${appPath}.unpacked`);
    }
  }
  return roots;
}

function candidatePackagedAegisCommands(options = {}) {
  const platform = options.platform || process.platform;
  const pathMod = _pathFor(platform);
  const roots = _packagedResourceRoots(options);
  const layouts = [
    [],
    ["bin"],
    ["Scripts"],
    ["aegis"],
    ["aegis", "bin"],
    ["aegis", "Scripts"],
    ["backend"],
    ["backend", "bin"],
    ["backend", "Scripts"],
    ["venv", "bin"],
    ["venv", "Scripts"],
  ];
  const candidates = [];
  for (const root of roots) {
    for (const layout of layouts) {
      for (const name of _commandNames(platform)) {
        candidates.push(pathMod.join(root, ...layout, name));
      }
    }
  }
  return Array.from(new Set(candidates.filter(Boolean)));
}

function packagedBackendPathEntries(options = {}) {
  const platform = options.platform || process.platform;
  const pathMod = _pathFor(platform);
  const exists = options.exists || fs.existsSync;
  const entries = [];
  for (const candidate of candidatePackagedAegisCommands({ ...options, platform })) {
    const dir = pathMod.dirname(candidate);
    try {
      if ((exists(candidate) || exists(dir)) && !entries.includes(dir)) entries.push(dir);
    } catch { /* ignore inaccessible packaged paths */ }
  }
  return entries;
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
  const userEnvBin = _userEnvValue("AEGIS_BIN", { ...options, env, platform });
  if (userEnvBin && userEnvBin !== explicit) candidates.push(userEnvBin);
  if (options.packaged || options.resourcesPath || options.appPath) {
    candidates.push(...candidatePackagedAegisCommands({ ...options, platform }));
  }
  const homes = [home];
  const userEnvHome = _userEnvValue("AEGIS_HOME", { ...options, env, platform });
  if (userEnvHome && !homes.some((item) => item.toLowerCase() === userEnvHome.toLowerCase())) {
    homes.push(userEnvHome);
  }
  if (platform === "win32") {
    for (const candidateHome of homes) {
      candidates.push(
        pathMod.join(candidateHome, "venv", "Scripts", "aegis.exe"),
        pathMod.join(candidateHome, "venv", "Scripts", "aegis.cmd"),
      );
    }
    candidates.push(
      pathMod.join(homedir, ".aegis", "venv", "Scripts", "aegis.exe"),
      pathMod.join(homedir, ".aegis", "venv", "Scripts", "aegis.cmd"),
    );
  }
  for (const candidateHome of homes) {
    candidates.push(pathMod.join(candidateHome, "venv", "bin", "aegis"));
  }
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
  const packagedPathEntries = packagedBackendPathEntries({ ...options, platform });
  const env = normalizePathEnv(_withWindowsUserPath(baseEnv, { ...options, platform }), {
    ...options,
    platform,
    prependPathEntries: packagedPathEntries,
  });
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
  candidatePackagedAegisCommands,
  desktopLogPath,
  hiddenWindowsChildOptions,
  normalizePathEnv,
  packagedBackendPathEntries,
  resolveAegisHome,
  sanePathEntries,
};
