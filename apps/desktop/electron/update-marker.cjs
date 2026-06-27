"use strict";

const fs = require("node:fs");
const path = require("node:path");

const UPDATE_MARKER_MAX_AGE_MS = 20 * 60 * 1000;
const UPDATE_MARKER_FILE = ".aegis-update-in-progress";

function markerPath(aegisHome) {
  return path.join(aegisHome, UPDATE_MARKER_FILE);
}

function isPidAlive(pid, kill = process.kill.bind(process)) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    kill(pid, 0);
    return true;
  } catch (error) {
    return Boolean(error && error.code === "EPERM");
  }
}

function readLiveUpdateMarker(aegisHome, options = {}) {
  const now = typeof options.now === "function" ? options.now : Date.now;
  const kill = options.kill;
  const maxAgeMs = Number.isFinite(options.maxAgeMs)
    ? options.maxAgeMs
    : UPDATE_MARKER_MAX_AGE_MS;
  const file = markerPath(aegisHome);
  let raw = "";
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch {
    return null;
  }

  const [pidLine, startedLine] = String(raw).split("\n");
  const pid = Number.parseInt(String(pidLine || "").trim(), 10);
  const startedAt = Number.parseInt(String(startedLine || "").trim(), 10);
  const ageMs = Number.isFinite(startedAt) ? now() - startedAt * 1000 : Infinity;
  const alive = isPidAlive(pid, kill || process.kill.bind(process));
  if (!alive || ageMs > maxAgeMs) {
    try {
      fs.unlinkSync(file);
    } catch {
      // best-effort cleanup only
    }
    return null;
  }
  return { pid, ageMs };
}

module.exports = {
  UPDATE_MARKER_FILE,
  UPDATE_MARKER_MAX_AGE_MS,
  isPidAlive,
  markerPath,
  readLiveUpdateMarker,
};
