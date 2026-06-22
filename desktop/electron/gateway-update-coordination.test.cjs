const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  markerPath,
  pauseGatewayForUpdate,
  resumeGatewayAfterUpdate,
} = require("./gateway-update-coordination.cjs");

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aegis-gateway-update-"));
}

test("gateway update pause skips non-windows and remote dashboards", () => {
  assert.deepEqual(
    pauseGatewayForUpdate({ platform: "linux", mode: "local", userData: tmpdir(), command: "aegis" }),
    { ok: true, skipped: true, reason: "non-windows" },
  );
  assert.deepEqual(
    pauseGatewayForUpdate({ platform: "win32", mode: "remote", userData: tmpdir(), command: "aegis" }),
    { ok: true, skipped: true, reason: "remote-dashboard" },
  );
});

test("gateway update pause stops windows service and writes resume marker", () => {
  const userData = tmpdir();
  const calls = [];
  const result = pauseGatewayForUpdate({
    platform: "win32",
    mode: "local",
    userData,
    command: "aegis.exe",
    spawnSync: (command, args, opts) => {
      calls.push({ command, args, opts });
      return { status: 0, stdout: "stopped", stderr: "" };
    },
  });

  assert.equal(result.ok, true);
  assert.equal(calls[0].command, "aegis.exe");
  assert.deepEqual(calls[0].args, ["gateway", "stop"]);
  assert.equal(calls[0].opts.windowsHide, true);
  const marker = JSON.parse(fs.readFileSync(markerPath(userData), "utf8"));
  assert.equal(marker.resume, true);
  assert.equal(marker.command, "aegis.exe");
});

test("gateway update pause failure leaves update ready", () => {
  const userData = tmpdir();
  const result = pauseGatewayForUpdate({
    platform: "win32",
    mode: "local",
    userData,
    command: "aegis.exe",
    spawnSync: () => ({ status: 1, stdout: "", stderr: "denied" }),
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /denied/);
  assert.equal(fs.existsSync(markerPath(userData)), false);
});

test("gateway update resume starts service and clears marker", () => {
  const userData = tmpdir();
  const marker = markerPath(userData);
  fs.writeFileSync(marker, JSON.stringify({ resume: true, command: "aegis.exe" }));
  const calls = [];

  const result = resumeGatewayAfterUpdate({
    platform: "win32",
    userData,
    spawnSync: (command, args) => {
      calls.push({ command, args });
      return { status: 0, stdout: "started", stderr: "" };
    },
  });

  assert.equal(result.ok, true);
  assert.equal(result.resumed, true);
  assert.deepEqual(calls, [{ command: "aegis.exe", args: ["gateway", "start"] }]);
  assert.equal(fs.existsSync(marker), false);
});
