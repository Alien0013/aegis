const fs = require("node:fs");
const path = require("node:path");
const { spawnSync: defaultSpawnSync } = require("node:child_process");

const MARKER = "gateway-update-resume.json";

function markerPath(userData) {
  return path.join(userData || process.cwd(), MARKER);
}

function writeMarker(userData, data) {
  const target = markerPath(userData);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, JSON.stringify({ ...data, writtenAt: new Date().toISOString() }));
  return target;
}

function readMarker(userData) {
  try {
    const target = markerPath(userData);
    return JSON.parse(fs.readFileSync(target, "utf8"));
  } catch {
    return null;
  }
}

function clearMarker(userData) {
  try {
    fs.rmSync(markerPath(userData), { force: true });
  } catch {
    // best effort
  }
}

function runGatewayCommand(command, action, spawnSync = defaultSpawnSync) {
  if (!command || command === "remote-dashboard") {
    return { ok: false, error: "local AEGIS command is not resolved" };
  }
  const result = spawnSync(command, ["gateway", action], {
    encoding: "utf8",
    timeout: 15000,
    windowsHide: true,
  });
  const stdout = String(result.stdout || "").trim();
  const stderr = String(result.stderr || "").trim();
  return {
    ok: result.status === 0,
    status: result.status,
    stdout,
    stderr,
    error: result.error ? String(result.error.message || result.error) : stderr,
  };
}

function pauseGatewayForUpdate(options = {}) {
  const platform = options.platform || process.platform;
  const mode = options.mode || "local";
  if (platform !== "win32") return { ok: true, skipped: true, reason: "non-windows" };
  if (mode === "remote") return { ok: true, skipped: true, reason: "remote-dashboard" };
  const result = runGatewayCommand(options.command, "stop", options.spawnSync);
  if (!result.ok) {
    return { ok: false, error: result.error || "gateway service could not be paused", result };
  }
  const marker = writeMarker(options.userData, { resume: true, command: options.command });
  return { ok: true, marker, result };
}

function resumeGatewayAfterUpdate(options = {}) {
  const platform = options.platform || process.platform;
  if (platform !== "win32") return { ok: true, skipped: true, reason: "non-windows" };
  const marker = readMarker(options.userData);
  if (!marker || !marker.resume) return { ok: true, skipped: true, reason: "no-marker" };
  const command = options.command || marker.command;
  const result = runGatewayCommand(command, "start", options.spawnSync);
  if (result.ok) {
    clearMarker(options.userData);
    return { ok: true, resumed: true, result };
  }
  return { ok: false, resumed: false, error: result.error || "gateway service could not be resumed", result };
}

module.exports = {
  markerPath,
  pauseGatewayForUpdate,
  resumeGatewayAfterUpdate,
  runGatewayCommand,
};
