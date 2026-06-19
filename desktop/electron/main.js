// AEGIS desktop — a solid native shell around the local AEGIS dashboard.
//
// Flow: show a splash instantly -> start the `aegis dashboard` backend on a
// random free port + token -> health-probe it (reporting progress to the
// splash) -> open the main window and swap it in when loaded. The backend is
// kept alive (restart-on-crash) and stopped cleanly on quit. Logs are captured
// so a failed boot can show the real error and an "Open logs" button.
const {
  app, BrowserWindow, Menu, Tray, Notification, shell, clipboard, ipcMain,
  nativeTheme, globalShortcut, powerMonitor, dialog,
} = require("electron");
const { spawn } = require("child_process");
const net = require("net");
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const {
  aegisCommand,
  backendEnvironment,
  desktopLogPath,
  resolveAegisCommand,
  resolveAegisHome,
} = require("./backend-env.cjs");
const {
  desktopRemoteConnection,
  desktopProjectCwd,
  readDesktopSettings,
  writeDesktopSettings,
} = require("./desktop-settings.cjs");
const {
  desktopDiagnostics,
  detectRemoteDisplay,
  readBackendManifest,
  readInstallStamp,
  releaseUpdateEligibility,
} = require("./desktop-status.cjs");
const {
  normalizeApiProxyMethod,
  normalizeApiProxyPath,
  serializeApiProxyBody,
} = require("./api-proxy.cjs");
const {
  initialUpdaterStatus,
  transitionUpdaterStatus,
} = require("./updater-status.cjs");

// Chromium checks the Linux setuid sandbox before main.js runs, so launch.js
// puts --no-sandbox on argv; mirror it here so child processes inherit it.
if (process.platform === "linux" && process.env.AEGIS_ELECTRON_SANDBOX !== "1") {
  app.commandLine.appendSwitch("no-sandbox");
}

const REMOTE_DISPLAY_REASON = detectRemoteDisplay();
if (REMOTE_DISPLAY_REASON) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu-compositing");
  console.log(`[aegis] remote display detected (${REMOTE_DISPLAY_REASON}); using software renderer`);
}

app.commandLine.appendSwitch("disable-renderer-backgrounding");
app.commandLine.appendSwitch("disable-backgrounding-occluded-windows");
app.commandLine.appendSwitch("disable-background-timer-throttling");

app.on("web-contents-created", (_event, contents) => {
  contents.on("will-attach-webview", (event) => event.preventDefault());
});

const MAX_CRASH_RESTARTS = 3;
let backend = null;          // child process
let splash = null;           // splash BrowserWindow
let win = null;              // main BrowserWindow
let port = 0;
let token = "";
let dashboardUrl = "";
let quitting = false;
let crashRestarts = 0;
let logFd = null;
let tray = null;                       // system-tray presence
let backendStartedAt = 0;
let backendCommand = "";
let backendArgs = [];
let backendEnvSummary = {};
let backendCwdSource = "";
let restartingBackend = false;
let lastBootPhase = null;
let autoUpdater = null;
let autoUpdaterConfigured = false;
let updateCheckInFlight = false;
let updateCheckManual = false;
let updaterStatus = initialUpdaterStatus();
const extraWindows = new Set();        // secondary session windows (multi-window)
const GLOBAL_SHOW_SHORTCUT = "CommandOrControl+Shift+A";
const DEEP_LINK_SCHEME = "aegis";      // aegis://chat , aegis://config , ...
let pendingDeepLink = "";              // a deep link that arrived before the window existed

/* ---------- paths & logging ---------- */
const logPath = () => desktopLogPath({ env: process.env, userData: app.getPath("userData") });
const stateFile = () => path.join(app.getPath("userData"), "window-state.json");

// The dashboard is a HashRouter SPA: deep-links must be `#/path` (leading slash).
// route("/app") -> chat-first desktop shell; route("/") -> the full control panel.
const route = (p) => dashboardUrl + "#" + (p && p.startsWith("/") ? p : "/" + (p || ""));
// The desktop app opens into the focused chat-first surface, not the admin grid.
const DEFAULT_ROUTE = "/app";
const localBackendBaseUrl = () => port ? `http://127.0.0.1:${port}` : "";

function remoteConnection() {
  return desktopRemoteConnection({ env: process.env, userData: app.getPath("userData") });
}

function backendBaseUrl() {
  const remote = remoteConnection();
  if (remote.enabled) return remote.url;
  return localBackendBaseUrl();
}

function dashboardUrlForBase(baseUrl, authToken = "") {
  if (!baseUrl) return "";
  const url = new URL(baseUrl);
  if (authToken) url.searchParams.set("token", authToken);
  return url.toString();
}

function websocketUrlForBase(baseUrl, authToken = "") {
  if (!baseUrl) return "";
  const url = new URL("/api/ws", baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (authToken) url.searchParams.set("token", authToken);
  return url.toString();
}

function requestTransport(url) {
  return url.protocol === "https:" ? https : http;
}

function log(line) {
  try {
    if (logFd === null) {
      const target = logPath();
      fs.mkdirSync(path.dirname(target), { recursive: true });
      logFd = fs.openSync(target, "a");
    }
    fs.writeSync(logFd, `[${new Date().toISOString()}] ${line}\n`);
  } catch { /* ignore */ }
}

function readRecentLogLines(maxLines = 200) {
  const limit = Math.max(1, Math.min(1000, Number(maxLines) || 200));
  try {
    return fs.readFileSync(logPath(), "utf8").split(/\r?\n/).slice(-limit);
  } catch {
    return [];
  }
}

function setUpdaterStatus(event, details = {}) {
  updaterStatus = transitionUpdaterStatus(updaterStatus, event, details);
  const summary = updaterStatus.message || updaterStatus.error || updaterStatus.stage;
  log(`updater status: ${updaterStatus.stage}${summary ? `: ${summary}` : ""}`);
  return updaterStatus;
}

function hiddenWindowsChildOptions(options = {}) {
  if (process.platform !== "win32" || Object.prototype.hasOwnProperty.call(options, "windowsHide")) {
    return options;
  }
  return { ...options, windowsHide: true };
}

function openExternalUrl(rawUrl) {
  const raw = String(rawUrl || "").trim();
  if (!raw) return false;
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    return false;
  }
  if (!["http:", "https:", "mailto:"].includes(parsed.protocol)) return false;
  shell.openExternal(parsed.toString()).catch((err) => log(`openExternal failed: ${err.message}`));
  return true;
}

function isInternalNavigationUrl(rawUrl) {
  try {
    const parsed = new URL(String(rawUrl || ""));
    return Boolean(backendBaseUrl() && parsed.origin === backendBaseUrl());
  } catch {
    return false;
  }
}

function wireRendererWindow(w) {
  w.webContents.setWindowOpenHandler(({ url }) => {
    openExternalUrl(url);
    return { action: "deny" };
  });
  w.webContents.on("will-navigate", (event, url) => {
    if (isInternalNavigationUrl(url)) return;
    event.preventDefault();
    openExternalUrl(url);
  });
}

/* ---------- backend resolution ---------- */
function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}
/* ---------- window-state persistence ---------- */
function loadState() {
  try { return JSON.parse(fs.readFileSync(stateFile(), "utf8")); } catch { return {}; }
}
function saveState() {
  if (!win || win.isDestroyed()) return;
  try {
    const b = win.getNormalBounds();
    fs.writeFileSync(stateFile(), JSON.stringify({ ...b, maximized: win.isMaximized() }));
  } catch { /* ignore */ }
}

/* ---------- splash ---------- */
function createSplash() {
  const current = new BrowserWindow({
    width: 460, height: 340, frame: false, resizable: false, show: false,
    backgroundColor: "#0b0d10", center: true, transparent: false, alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  splash = current;
  current.loadFile(path.join(__dirname, "boot.html"));
  current.webContents.once("did-finish-load", () => {
    if (lastBootPhase && !current.isDestroyed()) current.webContents.send("boot:status", lastBootPhase);
  });
  current.once("ready-to-show", () => !current.isDestroyed() && current.show());
  current.on("closed", () => { if (splash === current) splash = null; });
}
function ensureSplash() {
  if (!splash || splash.isDestroyed()) createSplash();
  return splash;
}
function boot(phase) {
  lastBootPhase = phase;
  if (splash && !splash.isDestroyed()) splash.webContents.send("boot:status", phase);
}

/* ---------- backend lifecycle ---------- */
function startBackend() {
  return new Promise(async (resolve, reject) => {
    const remote = remoteConnection();
    if (remote.enabled) {
      port = 0;
      token = remote.token || "";
      dashboardUrl = dashboardUrlForBase(remote.url, token);
      backend = null;
      backendCommand = "remote-dashboard";
      backendArgs = [];
      backendStartedAt = Date.now();
      backendCwdSource = remote.source || "remote";
      backendEnvSummary = {
        remote: true,
        AEGIS_DESKTOP_REMOTE_URL: remote.url,
        AEGIS_DESKTOP_REMOTE_TOKEN: remote.tokenConfigured ? "[configured]" : "",
      };
      log(`using remote dashboard: ${remote.url} (${backendCwdSource})`);
      resolve();
      return;
    }
    port = await freePort();
    token = crypto.randomBytes(18).toString("hex");
    dashboardUrl = dashboardUrlForBase(localBackendBaseUrl(), token);
    const desktopSettings = readDesktopSettings({ userData: app.getPath("userData") });
    const settingsBackendEnv = desktopSettings.backendEnv || {};
    const launchEnv = {
      ...process.env,
      ...(settingsBackendEnv.AEGIS_HOME ? { AEGIS_HOME: settingsBackendEnv.AEGIS_HOME } : {}),
      ...(settingsBackendEnv.AEGIS_BIN ? { AEGIS_BIN: settingsBackendEnv.AEGIS_BIN } : {}),
    };
    const cwdChoice = desktopProjectCwd({
      env: launchEnv,
      userData: app.getPath("userData"),
      cwd: process.cwd(),
    });
    const backendOptions = {
      cwd: cwdChoice.cwd,
      packaged: app.isPackaged,
      resourcesPath: process.resourcesPath || "",
      appPath: typeof app.getAppPath === "function" ? app.getAppPath() : "",
    };
    const resolvedEnv = backendEnvironment(launchEnv, backendOptions);
    const bin = aegisCommand({ ...backendOptions, env: resolvedEnv });
    const resolvedBin = bin !== "aegis" ? bin : (resolvedEnv.AEGIS_BIN || "");
    backendCommand = bin;
    backendArgs = ["dashboard", "--host", "127.0.0.1", "--port", String(port), "--no-open"];
    backendStartedAt = Date.now();
    backendCwdSource = cwdChoice.source;
    backendEnvSummary = {
      AEGIS_HOME: resolvedEnv.AEGIS_HOME || resolveAegisHome({ env: resolvedEnv }),
      AEGIS_BIN: resolvedBin,
      TERMINAL_CWD: resolvedEnv.TERMINAL_CWD || cwdChoice.cwd,
      packaged: app.isPackaged,
      resourcesPath: backendOptions.resourcesPath,
    };
    log(`starting backend: ${bin} dashboard --host 127.0.0.1 --port ${port}`);
    backend = spawn(bin, backendArgs, hiddenWindowsChildOptions({
      env: {
        ...resolvedEnv,
        ...(resolvedBin ? { AEGIS_BIN: resolvedBin } : {}),
        AEGIS_DASHBOARD_TOKEN: token,
        AEGIS_DESKTOP: "1",
        TERMINAL_CWD: resolvedEnv.TERMINAL_CWD || cwdChoice.cwd,
      },
      stdio: ["ignore", "pipe", "pipe"],
    }));
    const tail = (buf) => log(String(buf).trimEnd());
    backend.stdout.on("data", tail);
    backend.stderr.on("data", tail);
    backend.on("error", (e) => { log(`spawn error: ${e.message}`); reject(e); });
    backend.on("exit", (code, sig) => {
      log(`backend exited code=${code} sig=${sig}`);
      backend = null;
      backendStartedAt = 0;
      if (quitting || restartingBackend) return;
      onBackendCrash();
    });
    // resolve immediately; health probing happens next
    setTimeout(resolve, 60);
  });
}

function probe(url, tries, onTick) {
  return new Promise((resolve, reject) => {
    const attempt = (n) => {
      onTick && onTick(tries - n, tries);
      const target = new URL(url);
      requestTransport(target).get(target, {
        headers: token ? { "X-Aegis-Token": token } : {},
      }, (r) => {
        let body = "";
        r.setEncoding("utf8");
        r.on("data", (chunk) => { body += chunk; });
        r.on("end", () => {
          if (r.statusCode >= 200 && r.statusCode < 300) {
            try {
              const data = JSON.parse(body || "{}");
              if (data.ok === true) { resolve(); return; }
            } catch { /* keep retrying */ }
          }
          if (n <= 0) reject(new Error("backend health check did not become ready"));
          else setTimeout(() => attempt(n - 1), 400);
        });
      }).on("error", () => {
        if (n <= 0) reject(new Error("backend did not respond in time"));
        else setTimeout(() => attempt(n - 1), 400);
      });
    };
    attempt(tries);
  });
}

function connectionDescriptor() {
  const baseUrl = backendBaseUrl();
  const remote = remoteConnection();
  const isRemote = remote.enabled;
  const running = isRemote || !!(backend && !backend.killed);
  const settings = readDesktopSettings({ userData: app.getPath("userData") });
  const descriptor = {
    baseUrl,
    mode: isRemote ? "remote" : "local",
    source: isRemote ? (remote.source || "remote") : "local",
    authMode: token ? "token" : "none",
    token,
    wsUrl: websocketUrlForBase(baseUrl, token),
    backend: {
      running,
      pid: running ? backend.pid : null,
      port,
      command: backendCommand,
      args: backendArgs,
      startedAt: backendStartedAt ? new Date(backendStartedAt).toISOString() : "",
      uptimeMs: backendStartedAt ? Date.now() - backendStartedAt : 0,
      crashRestarts,
      maxCrashRestarts: MAX_CRASH_RESTARTS,
      logPath: logPath(),
      userDataPath: app.getPath("userData"),
      cwdSource: backendCwdSource,
      env: backendEnvSummary,
      remote: isRemote
        ? {
            url: remote.url,
            source: remote.source || "remote",
            tokenConfigured: remote.tokenConfigured,
          }
        : null,
    },
    settings: {
      ...settings,
      explicitLaunchCwd: Boolean(process.env.TERMINAL_CWD),
      settingsPath: path.join(app.getPath("userData"), "desktop-settings.json"),
    },
    desktop: desktopDiagnostics({
      app,
      env: {
        ...process.env,
        AEGIS_HOME: backendEnvSummary.AEGIS_HOME || process.env.AEGIS_HOME || "",
        AEGIS_BIN: backendEnvSummary.AEGIS_BIN || process.env.AEGIS_BIN || "",
      },
      resourcesPath: process.resourcesPath || "",
      desktopRoot: path.join(__dirname, ".."),
      updaterStatus,
    }),
  };
  descriptor.desktop.updater = { ...updaterStatus };
  return descriptor;
}

function persistDesktopProjectDir(value) {
  const settings = writeDesktopSettings(
    { defaultProjectDir: value },
    { userData: app.getPath("userData") },
  );
  log(`desktop settings: defaultProjectDir=${settings.defaultProjectDir || "(cleared)"}`);
  return { ok: true, settings: connectionDescriptor().settings };
}

async function chooseDesktopProjectDir() {
  const current = readDesktopSettings({ userData: app.getPath("userData") }).defaultProjectDir;
  const result = await dialog.showOpenDialog(win && !win.isDestroyed() ? win : undefined, {
    title: "Choose default project directory",
    defaultPath: current || backendEnvSummary.TERMINAL_CWD || process.cwd(),
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || !result.filePaths.length) {
    return { ok: false, cancelled: true, settings: connectionDescriptor().settings };
  }
  return persistDesktopProjectDir(result.filePaths[0]);
}

function persistDesktopBackendEnv(values = {}) {
  const current = readDesktopSettings({ userData: app.getPath("userData") });
  const settings = writeDesktopSettings(
    { backendEnv: { ...(current.backendEnv || {}), ...values } },
    { userData: app.getPath("userData") },
  );
  const changed = Object.keys(values).filter((key) => values[key]);
  log(`desktop settings: backendEnv=${changed.length ? changed.join(",") : "(cleared)"}`);
  return { ok: true, settings: connectionDescriptor().settings };
}

function persistDesktopRemoteBackend(values = {}) {
  const current = readDesktopSettings({ userData: app.getPath("userData") });
  const settings = writeDesktopSettings(
    { remoteBackend: { ...(current.remoteBackend || {}), ...values } },
    { userData: app.getPath("userData") },
  );
  log(`desktop settings: remoteBackend=${settings.remoteBackend.url || "(cleared)"}`);
  setTimeout(() => restartFromScratch(), 0);
  return { ok: true, settings: connectionDescriptor().settings, restarting: true };
}

async function chooseBackendEnvTarget() {
  const parent = win && !win.isDestroyed() ? win : undefined;
  const choice = await dialog.showMessageBox(parent, {
    type: "question",
    buttons: ["AEGIS_HOME", "AEGIS_BIN", "Cancel"],
    defaultId: 0,
    cancelId: 2,
    message: "Choose backend pointer",
    detail: "AEGIS_HOME points to an install directory. AEGIS_BIN points to a specific aegis executable.",
  });
  if (choice.response === 2) return { cancelled: true };
  const key = choice.response === 1 ? "AEGIS_BIN" : "AEGIS_HOME";
  const result = await dialog.showOpenDialog(parent, {
    title: key === "AEGIS_BIN" ? "Choose AEGIS executable" : "Choose AEGIS home",
    properties: key === "AEGIS_BIN" ? ["openFile"] : ["openDirectory", "createDirectory"],
  });
  if (result.canceled || !result.filePaths.length) return { cancelled: true, key };
  return { key, value: result.filePaths[0] };
}

async function runDesktopRepairAction(action) {
  const id = typeof action === "string" ? action : String((action && action.id) || "");
  if (id === "open_logs") {
    const error = await shell.openPath(logPath());
    return { ok: !error, action: id, path: logPath(), ...(error ? { error } : {}) };
  }
  if (id === "restart_backend") {
    setTimeout(() => restartFromScratch(), 0);
    return { ok: true, action: id, restarting: true };
  }
  if (id === "set_backend_env") {
    const selected = await chooseBackendEnvTarget();
    if (selected.cancelled) return { ok: false, action: id, cancelled: true };
    const result = persistDesktopBackendEnv({ [selected.key]: selected.value });
    setTimeout(() => restartFromScratch(), 0);
    return {
      ok: true,
      action: id,
      key: selected.key,
      value: selected.value,
      settings: result.settings,
      restarting: true,
    };
  }
  if (id === "check_updates") {
    return { ok: true, action: id, status: initAutoUpdate(true) };
  }
  if (id === "install_update") {
    return { action: id, ...installDownloadedUpdate() };
  }
  return { ok: false, action: id, error: `unknown repair action: ${id || "<missing>"}` };
}

function runtimeDiagnostics() {
  const descriptor = connectionDescriptor();
  return {
    mode: descriptor.mode,
    source: descriptor.source,
    baseUrl: descriptor.baseUrl,
    authMode: descriptor.authMode,
    backend: descriptor.backend,
    desktop: descriptor.desktop,
    logs: {
      path: logPath(),
      recent: readRecentLogLines(80),
    },
  };
}

function apiRequest({ method = "GET", path: requestPath = "", body = null } = {}) {
  return new Promise((resolve, reject) => {
    let cleanPath;
    let methodName;
    let payload;
    let baseUrl;
    try {
      baseUrl = backendBaseUrl();
      if (!baseUrl) throw new Error("backend is not connected");
      cleanPath = normalizeApiProxyPath(requestPath);
      methodName = normalizeApiProxyMethod(method);
      payload = serializeApiProxyBody(body);
    } catch (error) {
      reject(error);
      return;
    }
    const url = new URL(`/api/${cleanPath}`, baseUrl);
    const req = requestTransport(url).request(url, {
      method: methodName,
      headers: {
        ...(token ? { "X-Aegis-Token": token } : {}),
        ...(payload ? { "Content-Type": "application/json", "Content-Length": String(payload.length) } : {}),
      },
    }, (res) => {
      let text = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { text += chunk; });
      res.on("end", () => {
        let parsed = text;
        try { parsed = text ? JSON.parse(text) : {}; } catch { /* return text */ }
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(parsed);
        else reject(new Error(typeof parsed === "string" ? parsed : (parsed.error || parsed.detail || `HTTP ${res.statusCode}`)));
      });
    });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

async function onBackendCrash() {
  if (quitting || restartingBackend) return;
  if (crashRestarts >= MAX_CRASH_RESTARTS) {
    ensureSplash();
    boot({ error: "The AEGIS backend stopped repeatedly. Open logs for details." });
    return;
  }
  crashRestarts += 1;
  log(`restarting backend (attempt ${crashRestarts}/${MAX_CRASH_RESTARTS})`);
  try {
    await startBackend();
    await probe(`${backendBaseUrl()}/api/health`, 50);
    if (win && !win.isDestroyed()) win.loadURL(route(DEFAULT_ROUTE));
  } catch (e) { log(`restart failed: ${e.message}`); onBackendCrash(); }
}

/* ---------- main window ---------- */
function createWindow() {
  const st = loadState();
  win = new BrowserWindow({
    width: st.width || 1320, height: st.height || 880,
    x: st.x, y: st.y, minWidth: 940, minHeight: 600, show: false,
    backgroundColor: "#0b0d10", title: "AEGIS",
    // Frameless on Linux/Windows — the React UI draws its own titlebar + window
    // controls for a native, on-brand feel. macOS keeps the inset traffic lights.
    frame: process.platform === "darwin",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    trafficLightPosition: process.platform === "darwin" ? { x: 14, y: 12 } : undefined,
    icon: process.platform === "linux" ? path.join(__dirname, "..", "build", "icon.png") : undefined,
    webPreferences: {
      preload: path.join(__dirname, "preload-app.js"),
      contextIsolation: true, nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });
  if (st.maximized) win.maximize();
  wireRendererWindow(win);
  for (const ev of ["resize", "move", "close"]) win.on(ev, saveState);
  const emitMax = () => { try { win.webContents.send("win:maximized", win.isMaximized()); } catch { /* ignore */ } };
  win.on("maximize", emitMax);
  win.on("unmaximize", emitMax);
  // Closing the window hides to the tray (the agent keeps running in the background);
  // quit explicitly from the tray/menu. Falls back to a real close if the tray is absent.
  win.on("close", (e) => {
    if (!quitting && tray) { e.preventDefault(); win.hide(); }
  });
  win.on("closed", () => { win = null; });
  return win;
}

/* ---------- native shell features ---------- */
function iconPath() {
  return path.join(__dirname, "..", "build", "icon.png");
}

// Native OS notification (e.g. backend ready / crashed). Clicking focuses the app
// and optionally navigates to a dashboard route.
function notify(title, body, gotoRoute) {
  try {
    if (!Notification.isSupported()) return;
    const n = new Notification({ title, body, icon: iconPath(), silent: false });
    n.on("click", () => { showMainWindow(); if (gotoRoute && win) win.loadURL(route(gotoRoute)); });
    n.show();
  } catch { /* ignore */ }
}

function showMainWindow() {
  if (!win || win.isDestroyed()) { if (!splash) run(); return; }
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

function toggleMainWindow() {
  if (win && !win.isDestroyed() && win.isVisible() && win.isFocused()) win.hide();
  else showMainWindow();
}

// A second dashboard window — work two sessions/views side by side.
function openExtraWindow(p) {
  if (!dashboardUrl) return;
  const w = new BrowserWindow({
    width: 1100, height: 780, minWidth: 820, minHeight: 540, show: false,
    backgroundColor: "#0b0d10", title: "AEGIS",
    frame: process.platform === "darwin",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    trafficLightPosition: process.platform === "darwin" ? { x: 14, y: 12 } : undefined,
    webPreferences: {
      preload: path.join(__dirname, "preload-app.js"),
      contextIsolation: true, nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });
  wireRendererWindow(w);
  const emitMax = () => { try { w.webContents.send("win:maximized", w.isMaximized()); } catch { /* ignore */ } };
  w.on("maximize", emitMax);
  w.on("unmaximize", emitMax);
  w.loadURL(route(p || "/"));
  w.once("ready-to-show", () => w.show());
  w.on("closed", () => extraWindows.delete(w));
  extraWindows.add(w);
  return w;
}

function createTray() {
  if (tray) return;
  try {
    tray = new Tray(iconPath());
  } catch (e) { log(`tray unavailable: ${e.message}`); return; }
  tray.setToolTip("AEGIS");
  const menu = Menu.buildFromTemplate([
    { label: "Show AEGIS", click: () => showMainWindow() },
    { label: "New Window", click: () => openExtraWindow("/app") },
    { type: "separator" },
    { label: "Chat", click: () => { showMainWindow(); win && win.loadURL(route("/app")); } },
    { label: "Sessions", click: () => { showMainWindow(); win && win.loadURL(route("/sessions")); } },
    { label: "Control Panel", click: () => { showMainWindow(); win && win.loadURL(route("/")); } },
    { type: "separator" },
    { label: "Open in Browser", click: () => dashboardUrl && openExternalUrl(dashboardUrl) },
    { label: "Restart Backend", click: () => restartFromScratch() },
    { label: "Quit AEGIS", click: () => { quitting = true; app.quit(); } },
  ]);
  tray.setContextMenu(menu);
  tray.on("click", () => toggleMainWindow());
}

// aegis://chat  ->  route "/chat". Tolerates aegis://chat, aegis:///chat, aegis://go/chat.
function deepLinkToRoute(url) {
  try {
    const rest = String(url).replace(/^aegis:\/+/i, "").replace(/^go\//i, "");
    const p = "/" + rest.replace(/^\/+/, "").split(/[?#]/)[0];
    return p === "/" ? "/" : p.replace(/\/+$/, "");
  } catch { return "/"; }
}

function handleDeepLink(url) {
  if (!url) return;
  const p = deepLinkToRoute(url);
  if (!dashboardUrl || !win || win.isDestroyed()) { pendingDeepLink = p; showMainWindow(); return; }
  showMainWindow();
  win.loadURL(route(p));
}

function registerGlobalShortcut() {
  try {
    if (!globalShortcut.isRegistered(GLOBAL_SHOW_SHORTCUT)) {
      globalShortcut.register(GLOBAL_SHOW_SHORTCUT, () => toggleMainWindow());
    }
  } catch (e) { log(`global shortcut failed: ${e.message}`); }
}

// Auto-update from GitHub Releases (electron-updater). Only meaningful in the
// packaged app; a source-run / dev build has no update feed. `manual` = the user
// asked from the menu, so report "up to date" / errors visibly.
function initAutoUpdate(manual) {
  updateCheckManual = Boolean(manual);
  if (!app.isPackaged) {
    const message = "Auto-update runs in the installed app only.";
    const status = setUpdaterStatus("disabled", { message });
    if (manual) notify("AEGIS updates", message);
    return status;
  }
  const updateEligibility = releaseUpdateEligibility({
    packaged: app.isPackaged,
    stamp: readInstallStamp(),
    backendManifest: readBackendManifest(),
    platform: process.platform,
    appVersion: app.getVersion(),
  });
  if (!updateEligibility.ok) {
    log(`auto-update disabled: ${updateEligibility.reason}`);
    const status = setUpdaterStatus("disabled", { reason: updateEligibility.reason });
    if (manual) notify("AEGIS updates", updateEligibility.reason);
    return status;
  }
  if (updateCheckInFlight) {
    if (manual) notify("AEGIS updates", "An update check is already running.");
    return updaterStatus;
  }
  try {
    if (!autoUpdater) ({ autoUpdater } = require("electron-updater"));
  }
  catch (e) {
    const message = `electron-updater unavailable: ${e.message}`;
    log(message);
    const status = setUpdaterStatus("error", { error: message });
    if (manual) notify("AEGIS update failed", message);
    return status;
  }
  if (!autoUpdaterConfigured) {
    autoUpdater.autoDownload = true;
    autoUpdater.allowPrerelease = false;
    autoUpdater.allowDowngrade = false;
    autoUpdater.removeAllListeners();
    autoUpdater.on("update-available", (info) => {
      setUpdaterStatus("available", { info });
      log(`update available: ${info.version}`);
      if (updateCheckManual) notify("AEGIS update", `Downloading ${info.version}...`);
    });
    autoUpdater.on("download-progress", (progress) => {
      setUpdaterStatus("progress", { progress });
    });
    autoUpdater.on("update-not-available", () => {
      setUpdaterStatus("current");
      if (updateCheckManual) notify("AEGIS", "You're on the latest version.");
    });
    autoUpdater.on("error", (e) => {
      setUpdaterStatus("error", { error: e });
      log(`updater error: ${e && e.message}`);
      if (updateCheckManual) notify("AEGIS update failed", String(e && e.message));
    });
    autoUpdater.on("update-downloaded", (info) => {
      setUpdaterStatus("ready", { info });
      log(`update downloaded: ${info.version}`);
      const choice = dialog.showMessageBoxSync(win && !win.isDestroyed() ? win : undefined, {
        type: "info", buttons: ["Restart now", "Later"], defaultId: 0, cancelId: 1,
        title: "Update ready", message: `AEGIS ${info.version} is ready to install.`,
        detail: "Restart AEGIS to apply the update.",
      });
      if (choice === 0) installDownloadedUpdate();
    });
    autoUpdaterConfigured = true;
  }
  updateCheckInFlight = true;
  const status = setUpdaterStatus("checking");
  autoUpdater.checkForUpdates()
    .catch((e) => {
      const message = String((e && e.message) || e || "Update check failed.");
      log(`checkForUpdates failed: ${message}`);
      setUpdaterStatus("error", { error: message });
      if (updateCheckManual) notify("AEGIS update failed", message);
    })
    .finally(() => { updateCheckInFlight = false; });
  return status;
}

function installDownloadedUpdate() {
  if (!autoUpdater || updaterStatus.stage !== "ready" || !updaterStatus.installable) {
    const message = "No downloaded update is ready to install.";
    return { ok: false, error: message, status: { ...updaterStatus } };
  }
  setUpdaterStatus("installing", { version: updaterStatus.version });
  quitting = true;
  autoUpdater.quitAndInstall();
  return { ok: true, status: { ...updaterStatus } };
}

/* ---------- boot sequence ---------- */
async function run() {
  ensureSplash();
  boot({ pct: 12, message: "Starting AEGIS backend…" });
  try {
    await startBackend();
    boot({ pct: 30, message: "Waiting for the agent to come online…" });
    await probe(`${backendBaseUrl()}/api/health`, 70, (done, total) =>
      boot({ pct: 30 + Math.round((done / total) * 55), message: "Waiting for the agent to come online…" }));
    boot({ pct: 90, message: "Opening AEGIS…" });
    createWindow();
    await win.loadURL(route(DEFAULT_ROUTE));
    boot({ pct: 100, message: "Ready" });
    win.show();
    if (splash && !splash.isDestroyed()) { splash.close(); splash = null; }
    installMenu();
    createTray();
    registerGlobalShortcut();
    if (crashRestarts === 0) notify("AEGIS is ready", "Your agent is online. ⌘/Ctrl+Shift+A to summon.");
    if (pendingDeepLink) { win.loadURL(route(pendingDeepLink)); pendingDeepLink = ""; }
    setTimeout(() => initAutoUpdate(false), 4000);   // quiet check shortly after launch
  } catch (e) {
    log(`boot failed: ${e.message}`);
    const desktopSettings = readDesktopSettings({ userData: app.getPath("userData") });
    const settingsBackendEnv = desktopSettings.backendEnv || {};
    const launchEnv = {
      ...process.env,
      ...(settingsBackendEnv.AEGIS_HOME ? { AEGIS_HOME: settingsBackendEnv.AEGIS_HOME } : {}),
      ...(settingsBackendEnv.AEGIS_BIN ? { AEGIS_BIN: settingsBackendEnv.AEGIS_BIN } : {}),
    };
    const backendOptions = {
      cwd: desktopProjectCwd({ env: launchEnv, userData: app.getPath("userData"), cwd: process.cwd() }).cwd,
      packaged: app.isPackaged,
      resourcesPath: process.resourcesPath || "",
      appPath: typeof app.getAppPath === "function" ? app.getAppPath() : "",
    };
    const resolvedEnv = backendEnvironment(launchEnv, backendOptions);
    const resolution = resolveAegisCommand({ ...backendOptions, env: resolvedEnv });
    const checked = Array.isArray(resolution.candidates) && resolution.candidates.length
      ? `Checked ${resolution.candidates.length} backend candidate(s).`
      : "No backend candidates were found.";
    boot({
      error: `${e.message}\n\nTried: ${resolution.command} dashboard\nBackend discovery: ${resolution.reason}\n${checked}\n\nRepair: install or repair the AEGIS CLI, set AEGIS_BIN to a working executable, or reinstall the desktop package with its bundled backend.`,
    });
  }
}

async function restartFromScratch() {
  if (restartingBackend) return;
  restartingBackend = true;
  crashRestarts = 0;
  const previousBackend = backend;
  backend = null;
  backendStartedAt = 0;
  try { if (previousBackend) previousBackend.kill(); } catch { /* ignore */ }
  if (win && !win.isDestroyed()) { win.destroy(); win = null; }
  ensureSplash();
  try {
    await run();
  } finally {
    restartingBackend = false;
  }
}

/* ---------- menu ---------- */
function installMenu() {
  const go = (p) => win && win.loadURL(route(p));
  const template = [
    { label: "AEGIS", submenu: [
      { label: "Chat", accelerator: "CmdOrCtrl+1", click: () => go("/app") },
      { label: "Sessions", accelerator: "CmdOrCtrl+2", click: () => go("/sessions") },
      { label: "Control Panel", accelerator: "CmdOrCtrl+3", click: () => go("/") },
      { label: "Settings", accelerator: "CmdOrCtrl+,", click: () => go("/config") },
      { type: "separator" },
      { label: "New Window", accelerator: "CmdOrCtrl+N", click: () => openExtraWindow("/app") },
      { label: "Open in Browser", click: () => dashboardUrl && openExternalUrl(dashboardUrl) },
      { label: "Copy Dashboard URL", click: () => dashboardUrl && clipboard.writeText(dashboardUrl) },
      { label: "Restart Backend", click: () => restartFromScratch() },
      { label: "Check for Updates…", click: () => initAutoUpdate(true) },
      { label: "Open Logs", click: () => shell.openPath(logPath()) },
      { type: "separator" },
      { role: process.platform === "darwin" ? "close" : "quit" },
    ]},
    { label: "Go", submenu: [
      { label: "Overview", click: () => go("/") },
      { label: "Chat", click: () => go("/chat") },
      { label: "Sessions", click: () => go("/sessions") },
      { type: "separator" },
      { label: "Models", click: () => go("/models") },
      { label: "Tools", click: () => go("/tools") },
      { label: "Skills", click: () => go("/skills") },
      { label: "Memory", click: () => go("/memory") },
      { type: "separator" },
      { label: "Scheduled (Cron)", click: () => go("/cron") },
      { label: "MCP Servers", click: () => go("/mcp") },
      { label: "Channels", click: () => go("/channels") },
      { label: "API Keys", click: () => go("/keys") },
      { label: "Files", click: () => go("/files") },
      { type: "separator" },
      { label: "Analytics", click: () => go("/analytics") },
      { label: "Logs", click: () => go("/logs") },
      { label: "System", click: () => go("/system") },
    ]},
    { label: "View", submenu: [
      { role: "reload" }, { role: "forceReload" }, { role: "toggleDevTools" },
      { type: "separator" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" },
      { type: "separator" }, { role: "togglefullscreen" },
    ]},
    { role: "editMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

/* ---------- ipc from splash ---------- */
ipcMain.on("boot:retry", () => restartFromScratch());
ipcMain.on("boot:openLogs", () => shell.openPath(logPath()));
ipcMain.on("boot:quit", () => { quitting = true; app.quit(); });

/* ---------- ipc from the frameless titlebar ---------- */
// Operate on whichever window sent the event so secondary windows work too.
const senderWindow = (e) => BrowserWindow.fromWebContents(e.sender);
ipcMain.on("win:minimize", (e) => { const w = senderWindow(e); if (w) w.minimize(); });
ipcMain.on("win:maximizeToggle", (e) => {
  const w = senderWindow(e);
  if (!w) return;
  if (w.isMaximized()) w.unmaximize(); else w.maximize();
});
ipcMain.on("win:close", (e) => { const w = senderWindow(e); if (w) w.close(); });
ipcMain.handle("win:isMaximized", (e) => !!(senderWindow(e) && senderWindow(e).isMaximized()));
ipcMain.on("win:openExternal", (_e, url) => { openExternalUrl(url); });
ipcMain.on("win:restartBackend", () => restartFromScratch());
ipcMain.handle("aegis:connection", () => connectionDescriptor());
ipcMain.handle("aegis:diagnostics", () => runtimeDiagnostics());
ipcMain.handle("aegis:repair", (_e, action) => runDesktopRepairAction(action));
ipcMain.handle("aegis:api", (_e, request) => apiRequest(request));
ipcMain.handle("aegis:logs:recent", (_e, options = {}) => readRecentLogLines(options && options.limit));
ipcMain.handle("aegis:logs:reveal", () => shell.openPath(logPath()));
ipcMain.handle("aegis:update:check", () => initAutoUpdate(true));
ipcMain.handle("aegis:update:status", () => ({ ...updaterStatus }));
ipcMain.handle("aegis:update:install", () => installDownloadedUpdate());
ipcMain.handle("aegis:settings:get", () => connectionDescriptor().settings);
ipcMain.handle("aegis:settings:setDefaultProjectDir", (_e, value) => persistDesktopProjectDir(value));
ipcMain.handle("aegis:settings:setRemoteBackend", (_e, value) => persistDesktopRemoteBackend(value || {}));
ipcMain.handle("aegis:settings:chooseProjectDir", () => chooseDesktopProjectDir());

/* ---------- deep links (aegis://) ---------- */
function pickDeepLink(argv) {
  return (argv || []).find((a) => typeof a === "string" && a.startsWith(`${DEEP_LINK_SCHEME}://`)) || "";
}
try {
  if (process.defaultApp && process.argv.length >= 2) {
    app.setAsDefaultProtocolClient(DEEP_LINK_SCHEME, process.execPath, [path.resolve(process.argv[1])]);
  } else {
    app.setAsDefaultProtocolClient(DEEP_LINK_SCHEME);
  }
} catch { /* ignore */ }

/* ---------- app lifecycle ---------- */
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", (_e, argv) => {
    const link = pickDeepLink(argv);
    if (link) { handleDeepLink(link); return; }
    const w = win || splash;
    if (w) { if (w.isMinimized()) w.restore(); w.focus(); } else { showMainWindow(); }
  });
  app.on("open-url", (e, url) => { e.preventDefault(); handleDeepLink(url); });  // macOS deep links
  app.setAboutPanelOptions({ applicationName: "AEGIS", applicationVersion: app.getVersion(),
    copyright: "MIT · Alien0013" });
  nativeTheme.themeSource = "dark";
  app.whenReady().then(() => {
    run();
    // Pause/resume the backend health story around sleep so a resumed laptop
    // re-probes instead of assuming the agent is still reachable.
    try {
      powerMonitor.on("resume", () => { log("system resumed"); if (win && !win.isDestroyed()) win.webContents.reloadIgnoringCache(); });
      powerMonitor.on("suspend", () => log("system suspended"));
    } catch { /* ignore */ }
    const startupLink = pickDeepLink(process.argv);
    if (startupLink) pendingDeepLink = deepLinkToRoute(startupLink);
  });
  app.on("activate", () => { if (!win && !splash) run(); else showMainWindow(); });
  app.on("window-all-closed", () => {
    // With a tray we stay resident (the agent keeps running); without one, quit.
    if (!tray && process.platform !== "darwin") app.quit();
  });
  app.on("will-quit", () => { try { globalShortcut.unregisterAll(); } catch { /* ignore */ } });
  app.on("before-quit", () => {
    quitting = true;
    try { globalShortcut.unregisterAll(); } catch { /* ignore */ }
    try { tray && tray.destroy(); } catch { /* ignore */ }
    try { backend && backend.kill(); } catch { /* ignore */ }
  });
}
