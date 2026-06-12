// AEGIS desktop app (Electron) — launches the local AEGIS dashboard server and
// opens it in a native window. The user double-clicks; no terminal needed.
//
// It spawns `aegis dashboard` (the Python backend) on a free port with a random
// token, waits for it to come up, then loads it. On quit it stops the server.
const { app, BrowserWindow, shell, dialog } = require("electron");
const { spawn } = require("child_process");
const net = require("net");
const http = require("http");

// On Linux, launch.js / electron-builder pass --no-sandbox before Chromium's
// early setuid-sandbox check. Keep this switch too so child processes inherit
// the same posture when main.js does run.
if (process.platform === "linux" && process.env.AEGIS_ELECTRON_SANDBOX !== "1") {
  app.commandLine.appendSwitch("no-sandbox");
}

let serverProc = null;
let win = null;

function freePort() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); });
  });
}

function randomToken() {
  return require("crypto").randomBytes(18).toString("hex");
}

// Resolve the aegis CLI: explicit env, then the venv next to ~/.aegis, then PATH.
function aegisCommand() {
  const os = require("os"), path = require("path"), fs = require("fs");
  if (process.env.AEGIS_BIN && fs.existsSync(process.env.AEGIS_BIN)) return process.env.AEGIS_BIN;
  const venv = path.join(os.homedir(), ".aegis", "venv", "bin", "aegis");
  if (fs.existsSync(venv)) return venv;
  return "aegis"; // rely on PATH
}

function waitForServer(url, tries = 60) {
  return new Promise((resolve, reject) => {
    const attempt = (n) => {
      http.get(url, (r) => { r.resume(); resolve(); })
        .on("error", () => { n <= 0 ? reject(new Error("server did not start")) : setTimeout(() => attempt(n - 1), 500); });
    };
    attempt(tries);
  });
}

async function start() {
  const port = await freePort();
  const token = randomToken();
  const bin = aegisCommand();
  // `aegis dashboard --no-open` runs the server without launching a browser; flags
  // fall back gracefully if unsupported (the window still points at the URL).
  serverProc = spawn(bin, ["dashboard", "--port", String(port), "--no-open"], {
    env: { ...process.env, AEGIS_DASHBOARD_TOKEN: token },
    stdio: "ignore",
  });
  serverProc.on("error", (e) => {
    dialog.showErrorBox("AEGIS", `Could not start the AEGIS server (${bin}).\n\n${e.message}\n\nInstall AEGIS first, or set AEGIS_BIN to the aegis executable.`);
    app.quit();
  });

  const base = `http://127.0.0.1:${port}`;
  win = new BrowserWindow({
    width: 1280, height: 860, minWidth: 900, minHeight: 600,
    backgroundColor: "#0b0c10", title: "AEGIS",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  // open external links in the system browser, keep app links in-window
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });

  try {
    await waitForServer(base + "/", 60);
    await win.loadURL(`${base}/?token=${token}`);
  } catch (e) {
    dialog.showErrorBox("AEGIS", "The AEGIS server didn't come up in time.\n" + e.message);
  }
  win.on("closed", () => { win = null; });
}

app.whenReady().then(start);
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => { if (serverProc) try { serverProc.kill(); } catch {} });
