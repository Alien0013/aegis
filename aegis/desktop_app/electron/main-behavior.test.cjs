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

test("auto-updater setup is idempotent and bounded", () => {
  requireSnippet("autoUpdaterConfigured");
  requireSnippet("updateCheckInFlight");
  requireSnippet("releaseUpdateEligibility");
  requireSnippet("auto-update disabled");
  requireSnippet("autoUpdater.allowPrerelease = false");
  requireSnippet("autoUpdater.allowDowngrade = false");
});
