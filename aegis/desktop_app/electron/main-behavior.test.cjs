const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");

function requireSnippet(needle) {
  assert.notEqual(source.indexOf(needle), -1, `missing main.js snippet: ${needle}`);
}

test("main process opts into remote-display and background renderer hardening", () => {
  requireSnippet("detectRemoteDisplay()");
  requireSnippet("app.disableHardwareAcceleration()");
  requireSnippet('appendSwitch("disable-gpu-compositing")');
  requireSnippet('appendSwitch("disable-renderer-backgrounding")');
  requireSnippet('appendSwitch("disable-backgrounding-occluded-windows")');
  requireSnippet('appendSwitch("disable-background-timer-throttling")');
  requireSnippet("backgroundThrottling: false");
  requireSnippet("sandbox: true");
  requireSnippet("will-attach-webview");
});

test("renderer navigation stays inside the dashboard origin", () => {
  requireSnippet("function openExternalUrl");
  requireSnippet("function isInternalNavigationUrl");
  requireSnippet("function wireRendererWindow");
  requireSnippet("setWindowOpenHandler");
  requireSnippet("will-navigate");
  requireSnippet('["http:", "https:", "mailto:"]');
});

test("background backend processes hide Windows consoles", () => {
  requireSnippet("function hiddenWindowsChildOptions");
  requireSnippet("spawn(bin, backendArgs, hiddenWindowsChildOptions({");
});

test("packaged backend launch uses Electron resource paths", () => {
  requireSnippet("packaged: app.isPackaged");
  requireSnippet("resourcesPath: process.resourcesPath");
  requireSnippet("app.getAppPath()");
  requireSnippet("resolvedBin");
});

test("restart and splash lifecycle is single-flight", () => {
  requireSnippet("restartingBackend");
  requireSnippet("lastBootPhase");
  requireSnippet("function ensureSplash");
  requireSnippet("if (restartingBackend) return;");
  requireSnippet("quitting || restartingBackend");
});

test("auto-updater setup is idempotent and bounded", () => {
  requireSnippet("autoUpdaterConfigured");
  requireSnippet("updateCheckInFlight");
  requireSnippet("releaseUpdateEligibility");
  requireSnippet("auto-update disabled");
  requireSnippet("autoUpdater.allowPrerelease = false");
  requireSnippet("autoUpdater.allowDowngrade = false");
});

test("renderer diagnostics and logs bridge is bounded", () => {
  const preload = fs.readFileSync(path.join(__dirname, "preload-app.js"), "utf8");

  requireSnippet("function readRecentLogLines");
  requireSnippet('ipcMain.handle("aegis:diagnostics"');
  requireSnippet('ipcMain.handle("aegis:logs:recent"');
  assert.notEqual(preload.indexOf("getDiagnostics"), -1);
  assert.notEqual(preload.indexOf("getRecentLogs: (limit)"), -1);
});
