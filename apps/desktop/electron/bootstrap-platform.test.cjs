"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  bundledRuntimeImportCheck,
  detectRemoteDisplay,
  isWindowsBinaryPathInWsl,
  isWslEnvironment,
} = require("./bootstrap-platform.cjs");

test("isWslEnvironment detects WSL signals only on Linux", () => {
  assert.equal(isWslEnvironment({ WSL_DISTRO_NAME: "Ubuntu" }, "linux"), true);
  assert.equal(isWslEnvironment({ WSL_INTEROP: "/run/WSL/1" }, "linux"), true);
  assert.equal(isWslEnvironment({}, "linux", "6.6.87-microsoft-standard-WSL2"), true);
  assert.equal(isWslEnvironment({}, "linux", "6.6.87-generic"), false);
  assert.equal(isWslEnvironment({ WSL_DISTRO_NAME: "Ubuntu" }, "darwin"), false);
});

test("isWindowsBinaryPathInWsl detects Windows launchers in WSL only", () => {
  assert.equal(isWindowsBinaryPathInWsl("/mnt/c/Tools/aegis.exe", { isWsl: true }), true);
  assert.equal(isWindowsBinaryPathInWsl("/mnt/c/Tools/aegis.cmd", { isWsl: true }), true);
  assert.equal(isWindowsBinaryPathInWsl("/mnt/c/Tools/aegis.bat", { isWsl: true }), true);
  assert.equal(isWindowsBinaryPathInWsl("/mnt/c/Tools/install.ps1", { isWsl: true }), true);
  assert.equal(isWindowsBinaryPathInWsl("/usr/local/bin/aegis", { isWsl: true }), false);
  assert.equal(isWindowsBinaryPathInWsl("/mnt/c/Tools/aegis.exe", { isWsl: false }), false);
});

test("bundledRuntimeImportCheck selects platform-specific PTY imports", () => {
  assert.equal(bundledRuntimeImportCheck("win32"), "import fastapi, uvicorn, winpty");
  assert.equal(bundledRuntimeImportCheck("linux"), "import fastapi, uvicorn, ptyprocess");
  assert.equal(bundledRuntimeImportCheck("darwin"), "import fastapi, uvicorn, ptyprocess");
});

test("detectRemoteDisplay keeps GPU enabled for local displays", () => {
  assert.equal(detectRemoteDisplay({ env: { DISPLAY: ":0" }, platform: "linux" }), null);
  assert.equal(detectRemoteDisplay({ env: { WAYLAND_DISPLAY: "wayland-0" }, platform: "linux" }), null);
  assert.equal(detectRemoteDisplay({ env: { SESSIONNAME: "Console" }, platform: "win32" }), null);
});

test("detectRemoteDisplay flags ssh, x11 forwarding, and rdp", () => {
  assert.equal(detectRemoteDisplay({ env: { SSH_TTY: "/dev/pts/0" }, platform: "linux" }), "ssh-session");
  assert.match(
    String(detectRemoteDisplay({ env: { DISPLAY: "localhost:10.0" }, platform: "linux" })),
    /x11-forwarding/,
  );
  assert.match(String(detectRemoteDisplay({ env: { SESSIONNAME: "RDP-Tcp#7" }, platform: "win32" })), /^rdp/);
});

test("detectRemoteDisplay honors the AEGIS GPU override both ways", () => {
  assert.match(
    String(detectRemoteDisplay({ env: { AEGIS_DESKTOP_DISABLE_GPU: "1", DISPLAY: ":0" }, platform: "linux" })),
    /AEGIS_DESKTOP_DISABLE_GPU/,
  );
  assert.equal(
    detectRemoteDisplay({
      env: { AEGIS_DESKTOP_DISABLE_GPU: "false", SSH_CONNECTION: "1.2.3.4 5 6.7.8.9 22" },
      platform: "linux",
    }),
    null,
  );
});
