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
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");

// Chromium checks the Linux setuid sandbox before main.js runs, so launch.js
// puts --no-sandbox on argv; mirror it here so child processes inherit it.
if (process.platform === "linux" && process.env.AEGIS_ELECTRON_SANDBOX !== "1") {
  app.commandLine.appendSwitch("no-sandbox");
}

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
const extraWindows = new Set();        // secondary session windows (multi-window)
const GLOBAL_SHOW_SHORTCUT = "CommandOrControl+Shift+A";
const DEEP_LINK_SCHEME = "aegis";      // aegis://chat , aegis://config , ...
let pendingDeepLink = "";              // a deep link that arrived before the window existed

/* ---------- paths & logging ---------- */
const logPath = () => path.join(app.getPath("userData"), "desktop.log");
const stateFile = () => path.join(app.getPath("userData"), "window-state.json");

// The dashboard is a HashRouter SPA: deep-links must be `#/path` (leading slash).
// route("/app") -> chat-first desktop shell; route("/") -> the full control panel.
const route = (p) => dashboardUrl + "#" + (p && p.startsWith("/") ? p : "/" + (p || ""));
// The desktop app opens into the focused chat-first surface, not the admin grid.
const DEFAULT_ROUTE = "/app";

function log(line) {
  try {
    if (logFd === null) logFd = fs.openSync(logPath(), "a");
    fs.writeSync(logFd, `[${new Date().toISOString()}] ${line}\n`);
  } catch { /* ignore */ }
}

/* ---------- backend resolution ---------- */
function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}
function aegisCommand() {
  if (process.env.AEGIS_BIN && fs.existsSync(process.env.AEGIS_BIN)) return process.env.AEGIS_BIN;
  const venv = path.join(os.homedir(), ".aegis", "venv", "bin", "aegis");
  if (fs.existsSync(venv)) return venv;
  return "aegis"; // rely on PATH
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
  splash = new BrowserWindow({
    width: 460, height: 340, frame: false, resizable: false, show: false,
    backgroundColor: "#0b0d10", center: true, transparent: false, alwaysOnTop: true,
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false },
  });
  splash.loadFile(path.join(__dirname, "boot.html"));
  splash.once("ready-to-show", () => splash && splash.show());
}
function boot(phase) {
  if (splash && !splash.isDestroyed()) splash.webContents.send("boot:status", phase);
}

/* ---------- backend lifecycle ---------- */
function startBackend() {
  return new Promise(async (resolve, reject) => {
    port = await freePort();
    token = crypto.randomBytes(18).toString("hex");
    dashboardUrl = `http://127.0.0.1:${port}/?token=${token}`;
    const bin = aegisCommand();
    log(`starting backend: ${bin} dashboard --port ${port}`);
    backend = spawn(bin, ["dashboard", "--port", String(port), "--no-open"], {
      env: { ...process.env, AEGIS_DASHBOARD_TOKEN: token, AEGIS_DESKTOP: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const tail = (buf) => log(String(buf).trimEnd());
    backend.stdout.on("data", tail);
    backend.stderr.on("data", tail);
    backend.on("error", (e) => { log(`spawn error: ${e.message}`); reject(e); });
    backend.on("exit", (code, sig) => {
      log(`backend exited code=${code} sig=${sig}`);
      backend = null;
      if (quitting) return;
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
      http.get(url, (r) => { r.resume(); resolve(); })
        .on("error", () => { n <= 0 ? reject(new Error("backend did not respond in time")) : setTimeout(() => attempt(n - 1), 400); });
    };
    attempt(tries);
  });
}

async function onBackendCrash() {
  if (quitting) return;
  if (crashRestarts >= MAX_CRASH_RESTARTS) {
    if (splash && !splash.isDestroyed())
      boot({ error: "The AEGIS backend stopped repeatedly. Open logs for details." });
    else if (win && !win.isDestroyed())
      win.loadFile(path.join(__dirname, "boot.html")).then(() =>
        boot({ error: "The AEGIS backend stopped. Open logs for details." }));
    return;
  }
  crashRestarts += 1;
  log(`restarting backend (attempt ${crashRestarts}/${MAX_CRASH_RESTARTS})`);
  try {
    await startBackend();
    await probe(`http://127.0.0.1:${port}/`, 50);
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
    },
  });
  if (st.maximized) win.maximize();
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
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
    },
  });
  w.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
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
    { label: "Open in Browser", click: () => dashboardUrl && shell.openExternal(dashboardUrl) },
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
  if (!app.isPackaged) {
    if (manual) notify("AEGIS updates", "Auto-update runs in the installed app only.");
    return;
  }
  let autoUpdater;
  try { ({ autoUpdater } = require("electron-updater")); }
  catch (e) { log(`electron-updater unavailable: ${e.message}`); return; }
  autoUpdater.autoDownload = true;
  autoUpdater.removeAllListeners();
  autoUpdater.on("update-available", (info) => {
    log(`update available: ${info.version}`);
    if (manual) notify("AEGIS update", `Downloading ${info.version}…`);
  });
  autoUpdater.on("update-not-available", () => { if (manual) notify("AEGIS", "You're on the latest version."); });
  autoUpdater.on("error", (e) => { log(`updater error: ${e && e.message}`); if (manual) notify("AEGIS update failed", String(e && e.message)); });
  autoUpdater.on("update-downloaded", (info) => {
    log(`update downloaded: ${info.version}`);
    const choice = dialog.showMessageBoxSync(win && !win.isDestroyed() ? win : undefined, {
      type: "info", buttons: ["Restart now", "Later"], defaultId: 0, cancelId: 1,
      title: "Update ready", message: `AEGIS ${info.version} is ready to install.`,
      detail: "Restart AEGIS to apply the update.",
    });
    if (choice === 0) { quitting = true; autoUpdater.quitAndInstall(); }
  });
  autoUpdater.checkForUpdates().catch((e) => log(`checkForUpdates failed: ${e && e.message}`));
}

/* ---------- boot sequence ---------- */
async function run() {
  createSplash();
  boot({ pct: 12, message: "Starting AEGIS backend…" });
  try {
    await startBackend();
    boot({ pct: 30, message: "Waiting for the agent to come online…" });
    await probe(`http://127.0.0.1:${port}/`, 70, (done, total) =>
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
    const bin = aegisCommand();
    boot({ error: `${e.message}\n\nTried: ${bin} dashboard\nMake sure AEGIS is installed (or set AEGIS_BIN).` });
  }
}

async function restartFromScratch() {
  crashRestarts = 0;
  try { if (backend) backend.kill(); } catch { /* ignore */ }
  backend = null;
  if (win && !win.isDestroyed()) { win.destroy(); win = null; }
  if (!splash || splash.isDestroyed()) createSplash();
  await run();
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
      { label: "Open in Browser", click: () => dashboardUrl && shell.openExternal(dashboardUrl) },
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
ipcMain.on("win:openExternal", (_e, url) => { if (url && /^https?:/i.test(String(url))) shell.openExternal(url); });
ipcMain.on("win:restartBackend", () => restartFromScratch());

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
