"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  UPDATE_MARKER_FILE,
  isPidAlive,
  markerPath,
  readLiveUpdateMarker,
} = require("./update-marker.cjs");

function tempHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aegis-update-marker-"));
}

test("marker path is AEGIS-branded", () => {
  assert.equal(markerPath("/tmp/aegis-home"), path.join("/tmp/aegis-home", UPDATE_MARKER_FILE));
  assert.equal(UPDATE_MARKER_FILE, ".aegis-update-in-progress");
});

test("pid liveness treats EPERM as alive and ESRCH as dead", () => {
  assert.equal(isPidAlive(42, () => undefined), true);
  assert.equal(isPidAlive(42, () => { const error = new Error("denied"); error.code = "EPERM"; throw error; }), true);
  assert.equal(isPidAlive(42, () => { const error = new Error("gone"); error.code = "ESRCH"; throw error; }), false);
  assert.equal(isPidAlive(0, () => undefined), false);
});

test("live marker returns pid and age", () => {
  const home = tempHome();
  const started = 1_700_000;
  fs.writeFileSync(markerPath(home), `123\n${started}\n`, "utf8");
  assert.deepEqual(
    readLiveUpdateMarker(home, {
      kill: () => undefined,
      now: () => started * 1000 + 2500,
    }),
    { pid: 123, ageMs: 2500 },
  );
});

test("dead or stale markers are ignored and removed", () => {
  const deadHome = tempHome();
  fs.writeFileSync(markerPath(deadHome), "456\n10\n", "utf8");
  assert.equal(readLiveUpdateMarker(deadHome, { kill: () => { throw Object.assign(new Error("gone"), { code: "ESRCH" }); } }), null);
  assert.equal(fs.existsSync(markerPath(deadHome)), false);

  const staleHome = tempHome();
  fs.writeFileSync(markerPath(staleHome), "789\n1\n", "utf8");
  assert.equal(readLiveUpdateMarker(staleHome, { kill: () => undefined, now: () => 60 * 60 * 1000 }), null);
  assert.equal(fs.existsSync(markerPath(staleHome)), false);
});
