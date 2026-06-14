// AEGIS desktop — a solid native shell around the local AEGIS dashboard.
//
// Flow: show a splash instantly -> start the `aegis dashboard` backend on a
// random free port + token -> health-probe it (reporting progress to the
// splash) -> open the main window and swap it in when loaded. The backend is
// kept alive (restart-on-crash) and stopped cleanly on quit. Logs are captured
// so a failed boot can show the real error and an "Open logs" button.
const { app, BrowserWindow, Menu, shell, clipboard, ipcMain, nativeTheme } = require("electron");
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

/* ---------- paths & logging ---------- */
const logPath = () => path.join(app.getPath("userData"), "desktop.log");
const stateFile = () => path.join(app.getPath("userData"), "window-state.json");

// The dashboard is a HashRouter SPA: deep-links must be `#/path` (leading slash).
// route("/") -> Overview; route("/chat") -> Chat, etc.
const route = (p) => dashboardUrl + "#" + (p && p.startsWith("/") ? p : "/" + (p || ""));

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
      env: { ...process.env, AEGIS_DASHBOARD_TOKEN: token },
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
    if (win && !win.isDestroyed()) win.loadURL(route("/"));
  } catch (e) { log(`restart failed: ${e.message}`); onBackendCrash(); }
}

/* ---------- main window ---------- */
function createWindow() {
  const st = loadState();
  win = new BrowserWindow({
    width: st.width || 1320, height: st.height || 880,
    x: st.x, y: st.y, minWidth: 940, minHeight: 600, show: false,
    backgroundColor: "#0b0d10", title: "AEGIS",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    icon: process.platform === "linux" ? path.join(__dirname, "..", "build", "icon.png") : undefined,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  if (st.maximized) win.maximize();
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  for (const ev of ["resize", "move", "close"]) win.on(ev, saveState);
  win.on("closed", () => { win = null; });
  return win;
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
    boot({ pct: 90, message: "Opening dashboard…" });
    createWindow();
    await win.loadURL(route("/"));
    boot({ pct: 100, message: "Ready" });
    win.show();
    if (splash && !splash.isDestroyed()) { splash.close(); splash = null; }
    installMenu();
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
      { label: "Home", accelerator: "CmdOrCtrl+1", click: () => go("/") },
      { label: "Chat", accelerator: "CmdOrCtrl+2", click: () => go("/chat") },
      { label: "Sessions", accelerator: "CmdOrCtrl+3", click: () => go("/sessions") },
      { label: "Settings", accelerator: "CmdOrCtrl+,", click: () => go("/config") },
      { type: "separator" },
      { label: "Open in Browser", click: () => dashboardUrl && shell.openExternal(dashboardUrl) },
      { label: "Copy Dashboard URL", click: () => dashboardUrl && clipboard.writeText(dashboardUrl) },
      { label: "Restart Backend", click: () => restartFromScratch() },
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

/* ---------- app lifecycle ---------- */
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const w = win || splash;
    if (w) { if (w.isMinimized()) w.restore(); w.focus(); }
  });
  app.setAboutPanelOptions({ applicationName: "AEGIS", applicationVersion: app.getVersion(),
    copyright: "MIT · Alien0013" });
  nativeTheme.themeSource = "dark";
  app.whenReady().then(run);
  app.on("activate", () => { if (!win && !splash) run(); });
  app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
  app.on("before-quit", () => { quitting = true; try { backend && backend.kill(); } catch { /* ignore */ } });
}
