const fs = require("fs");
const os = require("os");
const path = require("path");

function settingsPath({ userData = "" } = {}) {
  return path.join(userData || os.homedir(), "desktop-settings.json");
}

function normalizeProjectDir(value) {
  return String(value || "").trim();
}

function normalizeBackendEnv(value) {
  const raw = value && typeof value === "object" ? value : {};
  return {
    AEGIS_HOME: normalizeProjectDir(raw.AEGIS_HOME),
    AEGIS_BIN: normalizeProjectDir(raw.AEGIS_BIN),
  };
}

function readDesktopSettings(options = {}) {
  const readFile = options.readFile || fs.readFileSync;
  try {
    const raw = JSON.parse(readFile(settingsPath(options), "utf8"));
    return {
      defaultProjectDir: normalizeProjectDir(raw.defaultProjectDir),
      backendEnv: normalizeBackendEnv(raw.backendEnv),
    };
  } catch {
    return { defaultProjectDir: "", backendEnv: normalizeBackendEnv() };
  }
}

function writeDesktopSettings(settings = {}, options = {}) {
  const target = settingsPath(options);
  const current = readDesktopSettings(options);
  const payload = {
    defaultProjectDir: Object.prototype.hasOwnProperty.call(settings, "defaultProjectDir")
      ? normalizeProjectDir(settings.defaultProjectDir)
      : current.defaultProjectDir,
    backendEnv: Object.prototype.hasOwnProperty.call(settings, "backendEnv")
      ? normalizeBackendEnv(settings.backendEnv)
      : current.backendEnv,
  };
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return payload;
}

function desktopProjectCwd(options = {}) {
  const env = options.env || process.env;
  const explicit = normalizeProjectDir(env.TERMINAL_CWD);
  if (explicit) {
    return {
      cwd: explicit,
      source: "env",
      settings: readDesktopSettings(options),
      explicitLaunchCwd: true,
    };
  }
  const settings = readDesktopSettings(options);
  const configured = normalizeProjectDir(settings.defaultProjectDir);
  const exists = options.exists || fs.existsSync;
  if (configured && exists(configured)) {
    return { cwd: configured, source: "desktop-settings", settings, explicitLaunchCwd: false };
  }
  return {
    cwd: options.cwd || process.cwd(),
    source: "process",
    settings,
    explicitLaunchCwd: false,
  };
}

module.exports = {
  desktopProjectCwd,
  normalizeBackendEnv,
  normalizeProjectDir,
  readDesktopSettings,
  settingsPath,
  writeDesktopSettings,
};
