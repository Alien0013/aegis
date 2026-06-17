const assert = require("node:assert/strict");
const test = require("node:test");
const {
  initialUpdaterStatus,
  transitionUpdaterStatus,
} = require("./updater-status.cjs");

const at = (iso) => () => new Date(iso);

test("updater status starts idle with bounded public fields", () => {
  assert.deepEqual(initialUpdaterStatus(at("2026-06-17T10:00:00.000Z")), {
    stage: "idle",
    message: "",
    error: "",
    version: "",
    checking: false,
    lastCheckedAt: "",
    updatedAt: "2026-06-17T10:00:00.000Z",
  });
});

test("updater status records checking and current outcomes", () => {
  let status = initialUpdaterStatus(at("2026-06-17T10:00:00.000Z"));
  status = transitionUpdaterStatus(status, "checking", {}, at("2026-06-17T10:00:01.000Z"));

  assert.equal(status.stage, "checking");
  assert.equal(status.checking, true);
  assert.equal(status.error, "");
  assert.equal(status.lastCheckedAt, "");

  status = transitionUpdaterStatus(status, "current", {}, at("2026-06-17T10:00:02.000Z"));

  assert.equal(status.stage, "current");
  assert.equal(status.checking, false);
  assert.equal(status.message, "You're on the latest version.");
  assert.equal(status.lastCheckedAt, "2026-06-17T10:00:02.000Z");
});

test("updater status preserves available version when update is ready", () => {
  let status = initialUpdaterStatus(at("2026-06-17T10:00:00.000Z"));
  status = transitionUpdaterStatus(
    status,
    "available",
    { info: { version: "1.2.3" } },
    at("2026-06-17T10:00:01.000Z"),
  );

  assert.equal(status.stage, "downloading");
  assert.equal(status.version, "1.2.3");
  assert.equal(status.message, "Downloading 1.2.3...");

  status = transitionUpdaterStatus(status, "ready", {}, at("2026-06-17T10:00:02.000Z"));

  assert.equal(status.stage, "ready");
  assert.equal(status.version, "1.2.3");
  assert.equal(status.message, "AEGIS 1.2.3 is ready to install.");
  assert.equal(status.lastCheckedAt, "2026-06-17T10:00:02.000Z");
});

test("updater status surfaces errors and disabled reasons", () => {
  let status = initialUpdaterStatus(at("2026-06-17T10:00:00.000Z"));
  status = transitionUpdaterStatus(
    status,
    "error",
    { error: new Error("network unavailable") },
    at("2026-06-17T10:00:01.000Z"),
  );

  assert.equal(status.stage, "error");
  assert.equal(status.error, "network unavailable");
  assert.equal(status.checking, false);
  assert.equal(status.lastCheckedAt, "2026-06-17T10:00:01.000Z");

  status = transitionUpdaterStatus(
    status,
    "disabled",
    { reason: "installed package is not stamped as a trusted release" },
    at("2026-06-17T10:00:02.000Z"),
  );

  assert.equal(status.stage, "disabled");
  assert.equal(status.message, "installed package is not stamped as a trusted release");
  assert.equal(status.error, "");
  assert.equal(status.checking, false);
});
