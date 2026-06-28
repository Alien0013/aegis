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
  requireSnippet("function openAgentsWindow");
  requireSnippet('ipcMain.handle("aegis:agents:openWindow"');
  requireSnippet("Live Agents Window");
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
  requireSnippet("resolveAegisCommand");
  requireSnippet('const resolvedBin = bin !== "aegis" ? bin : (resolvedEnv.AEGIS_BIN || "");');
  requireSnippet("Backend discovery:");
  requireSnippet("Repair: install or repair the AEGIS CLI");
});

test("remote dashboard mode skips local backend spawn", () => {
  requireSnippet("desktopRemoteConnection");
  requireSnippet("function remoteConnection()");
  requireSnippet("remote.enabled");
  requireSnippet("remote-dashboard");
  requireSnippet("dashboardUrlForBase(remote.url, token)");
  requireSnippet('mode: isRemote ? "remote" : "local"');
  requireSnippet("websocketUrlForBase(baseUrl, token)");
  requireSnippet("requestTransport(target).get");
  requireSnippet("requestTransport(url).request");
  requireSnippet("function persistDesktopRemoteBackend");
  requireSnippet('ipcMain.handle("aegis:settings:setRemoteBackend"');
});

test("restart and splash lifecycle is single-flight", () => {
  requireSnippet("restartingBackend");
  requireSnippet("lastBootPhase");
  requireSnippet("desktopLifecycle");
  requireSnippet("initialDesktopLifecycle()");
  requireSnippet("function setDesktopLifecycle");
  requireSnippet("transitionDesktopLifecycle");
  requireSnippet("lifecyclePublicSnapshot");
  requireSnippet("descriptor.desktop.lifecycle");
  requireSnippet('setDesktopLifecycle("probing_backend"');
  requireSnippet('setDesktopLifecycle("remote_mode"');
  requireSnippet('? "remote_mode" : "ready"');
  requireSnippet('setDesktopLifecycle("crashed"');
  requireSnippet('setDesktopLifecycle("repairing"');
  requireSnippet('setDesktopLifecycle("stopped"');
  requireSnippet("function ensureSplash");
  requireSnippet("if (restartingBackend) return;");
  requireSnippet("quitting || restartingBackend");
});

test("boot flow adopts the dashboard session token served by the backend", () => {
  requireSnippet('require("./dashboard-token.cjs")');
  requireSnippet("function adoptLocalDashboardToken");
  requireSnippet("adoptServedDashboardToken(backendBaseUrl(), token");
  requireSnippet("childAlive: () => Boolean(backend && !backend.killed && backend.exitCode === null)");
  requireSnippet("dashboardUrl = dashboardUrlForBase(localBackendBaseUrl(), token);");
  requireSnippet("await adoptLocalDashboardToken();");
});

test("auto-updater setup is idempotent and bounded", () => {
  requireSnippet("autoUpdaterConfigured");
  requireSnippet("updateCheckInFlight");
  requireSnippet("let updaterStatus = initialUpdaterStatus();");
  requireSnippet("pauseGatewayForUpdate");
  requireSnippet("resumeGatewayAfterUpdate");
  requireSnippet("function setUpdaterStatus");
  requireSnippet("function installDownloadedUpdate");
  requireSnippet("const gatewayPause = pauseGatewayForUpdate({");
  requireSnippet("const gatewayResume = resumeGatewayAfterUpdate({");
  requireSnippet("descriptor.desktop.updater = { ...updaterStatus };");
  requireSnippet("releaseUpdateEligibility");
  requireSnippet("appVersion: app.getVersion()");
  requireSnippet("auto-update disabled");
  requireSnippet('setUpdaterStatus("disabled", { reason: updateEligibility.reason });');
  requireSnippet('setUpdaterStatus("checking");');
  requireSnippet('setUpdaterStatus("available", { info });');
  requireSnippet('setUpdaterStatus("ready", { info });');
  requireSnippet('setUpdaterStatus("installing", { version: updaterStatus.version });');
  requireSnippet('setUpdaterStatus("error", { error: message });');
  requireSnippet("autoUpdater.allowPrerelease = false");
  requireSnippet("autoUpdater.allowDowngrade = false");
  requireSnippet("autoUpdater.autoInstallOnAppQuit = false");
  requireSnippet('if (updateCheckManual) notify("AEGIS update failed", message);');
  requireSnippet('if (id === "check_updates")');
  requireSnippet("status: initAutoUpdate(true)");
  requireSnippet('if (id === "install_update")');
  requireSnippet("installDownloadedUpdate()");
  requireSnippet('ipcMain.handle("aegis:update:check"');
  requireSnippet('ipcMain.handle("aegis:update:status"');
  requireSnippet('ipcMain.handle("aegis:update:install"');
});

test("renderer diagnostics and logs bridge is bounded", () => {
  const preload = fs.readFileSync(path.join(__dirname, "preload-app.js"), "utf8");

  requireSnippet('require("./api-proxy.cjs")');
  requireSnippet("normalizeApiProxyPath(requestPath)");
  requireSnippet("normalizeApiProxyMethod(method)");
  requireSnippet("serializeApiProxyBody(body)");
  requireSnippet("function readRecentLogLines");
  requireSnippet('ipcMain.handle("aegis:diagnostics"');
  requireSnippet('ipcMain.handle("aegis:repair"');
  requireSnippet('ipcMain.handle("aegis:logs:recent"');
  requireSnippet('ipcMain.handle("aegis:settings:get"');
  requireSnippet('ipcMain.handle("aegis:settings:setDefaultProjectDir"');
  requireSnippet('ipcMain.handle("aegis:settings:setRemoteBackend"');
  requireSnippet('ipcMain.handle("aegis:settings:chooseProjectDir"');
  requireSnippet("desktopProjectCwd({");
  assert.notEqual(preload.indexOf("getDiagnostics"), -1);
  assert.notEqual(preload.indexOf("openAgentsWindow"), -1);
  assert.notEqual(preload.indexOf("runRepairAction"), -1);
  assert.notEqual(preload.indexOf("getRecentLogs: (limit)"), -1);
  assert.notEqual(preload.indexOf("getSettings"), -1);
  assert.notEqual(preload.indexOf("setDefaultProjectDir"), -1);
  assert.notEqual(preload.indexOf("setRemoteBackend"), -1);
  assert.notEqual(preload.indexOf("chooseProjectDir"), -1);
  assert.notEqual(preload.indexOf("checkForUpdates"), -1);
  assert.notEqual(preload.indexOf("getUpdateStatus"), -1);
  assert.notEqual(preload.indexOf("installUpdate"), -1);
});
