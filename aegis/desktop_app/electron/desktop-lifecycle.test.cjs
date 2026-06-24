"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  initialDesktopLifecycle,
  lifecyclePublicSnapshot,
  transitionDesktopLifecycle,
} = require("./desktop-lifecycle.cjs");

const at = (value) => new Date(value);

test("desktop lifecycle exposes reference-style named states", () => {
  let lifecycle = initialDesktopLifecycle(at("2026-06-17T10:00:00.000Z"));
  assert.equal(lifecycle.state, "booting");
  assert.equal(lifecycle.events.length, 1);

  lifecycle = transitionDesktopLifecycle(
    lifecycle,
    "probing-backend",
    { message: "Waiting for health", port: 4821, backendPid: 123 },
    at("2026-06-17T10:00:01.000Z"),
  );
  assert.equal(lifecycle.state, "probing_backend");
  assert.equal(lifecycle.port, 4821);
  assert.equal(lifecycle.backendPid, 123);

  lifecycle = transitionDesktopLifecycle(
    lifecycle,
    "ready",
    { message: "Ready", mode: "local" },
    at("2026-06-17T10:00:02.000Z"),
  );
  assert.equal(lifecycle.state, "ready");
  assert.equal(lifecycle.mode, "local");

  const snapshot = lifecyclePublicSnapshot(lifecycle);
  assert.equal(snapshot.state, "ready");
  assert.equal(snapshot.events.length, 3);
  assert.deepEqual(snapshot.crashHistory, []);
});

test("desktop lifecycle records bounded crash history", () => {
  let lifecycle = initialDesktopLifecycle(at("2026-06-17T10:00:00.000Z"));
  for (let i = 0; i < 25; i += 1) {
    lifecycle = transitionDesktopLifecycle(
      lifecycle,
      "crashed",
      {
        message: `backend stopped ${i}`,
        code: i,
        signal: i % 2 ? "SIGTERM" : "",
        restartAttempt: i,
        maxCrashRestarts: 3,
      },
      at(`2026-06-17T10:${String(i + 1).padStart(2, "0")}:00.000Z`),
    );
  }
  assert.equal(lifecycle.crashHistory.length, 20);
  assert.equal(lifecycle.crashHistory[0].code, 5);
  assert.equal(lifecycle.crashHistory.at(-1).code, 24);

  const snapshot = lifecyclePublicSnapshot(lifecycle);
  assert.equal(snapshot.crashHistory.length, 8);
  assert.equal(snapshot.crashHistory[0].code, 17);
  assert.equal(snapshot.events.length, 12);
});

test("desktop lifecycle keeps update and repair state public but bounded", () => {
  let lifecycle = initialDesktopLifecycle(at("2026-06-17T10:00:00.000Z"));
  lifecycle = transitionDesktopLifecycle(
    lifecycle,
    "updating",
    { updateStage: "checking", message: "Checking for updates" },
    at("2026-06-17T10:00:01.000Z"),
  );
  lifecycle = transitionDesktopLifecycle(
    lifecycle,
    "repairing",
    { phase: "restart_backend", message: "Restarting backend" },
    at("2026-06-17T10:00:02.000Z"),
  );
  const snapshot = lifecyclePublicSnapshot(lifecycle);
  assert.equal(snapshot.state, "repairing");
  assert.equal(snapshot.phase, "restart_backend");
  assert.equal(snapshot.events[1].updateStage, "checking");
});
