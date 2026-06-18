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

function normalizeRemoteUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    if (!["http:", "https:"].includes(parsed.protocol)) return "";
    parsed.hash = "";
    return parsed.toString().replace(/\/$/, "");
  } catch {
    return "";
  }
}

function normalizeRemoteBackend(value) {
  const raw = value && typeof value === "object" ? value : {};
  return {
    url: normalizeRemoteUrl(raw.url || raw.baseUrl || raw.base_url),
    token: String(raw.token || raw.dashboardToken || raw.dashboard_token || "").trim(),
  };
}

function readDesktopSettings(options = {}) {
  const readFile = options.readFile || fs.readFileSync;
  try {
    const raw = JSON.parse(readFile(settingsPath(options), "utf8"));
    return {
      defaultProjectDir: normalizeProjectDir(raw.defaultProjectDir),
      backendEnv: normalizeBackendEnv(raw.backendEnv),
      remoteBackend: normalizeRemoteBackend(raw.remoteBackend),
    };
  } catch {
    return {
      defaultProjectDir: "",
      backendEnv: normalizeBackendEnv(),
      remoteBackend: normalizeRemoteBackend(),
    };
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
    remoteBackend: Object.prototype.hasOwnProperty.call(settings, "remoteBackend")
      ? normalizeRemoteBackend(settings.remoteBackend)
      : current.remoteBackend,
  };
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return payload;
}

function desktopRemoteConnection(options = {}) {
  const env = options.env || process.env;
  const settings = readDesktopSettings(options);
  const envUrl = normalizeRemoteUrl(env.AEGIS_DESKTOP_REMOTE_URL);
  const settingsUrl = normalizeRemoteUrl(settings.remoteBackend && settings.remoteBackend.url);
  const url = envUrl || settingsUrl;
  const token = String(
    env.AEGIS_DESKTOP_REMOTE_TOKEN
    || (settings.remoteBackend && settings.remoteBackend.token)
    || "",
  ).trim();
  return {
    enabled: Boolean(url),
    url,
    token,
    tokenConfigured: Boolean(token),
    source: envUrl ? "env" : settingsUrl ? "desktop-settings" : "",
    settings,
  };
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
  desktopRemoteConnection,
  desktopProjectCwd,
  normalizeBackendEnv,
  normalizeProjectDir,
  normalizeRemoteBackend,
  normalizeRemoteUrl,
  readDesktopSettings,
  settingsPath,
  writeDesktopSettings,
};
