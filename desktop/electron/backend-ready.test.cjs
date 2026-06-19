const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const test = require("node:test");

const { READY_RE, waitForDashboardPort } = require("./backend-ready.cjs");

function fakeChild() {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  return child;
}

test("waitForDashboardPort resolves announced AEGIS dashboard port", async () => {
  const child = fakeChild();
  const promise = waitForDashboardPort(child, 1000);

  child.stdout.write("booting\nAEGIS_DASH");
  child.stdout.write("BOARD_READY port=9123\n");

  assert.equal(await promise, 9123);
  assert.equal(child.stdout.listenerCount("data"), 0);
  assert.equal(child.listenerCount("exit"), 0);
  assert.equal(child.listenerCount("error"), 0);
  assert.match("AEGIS_DASHBOARD_READY port=1", READY_RE);
});

test("waitForDashboardPort rejects when backend exits before ready", async () => {
  const child = fakeChild();
  const promise = waitForDashboardPort(child, 1000);

  child.emit("exit", 7, null);

  await assert.rejects(promise, /exited before readiness announcement \(7\)/);
  assert.equal(child.stdout.listenerCount("data"), 0);
  assert.equal(child.listenerCount("exit"), 0);
  assert.equal(child.listenerCount("error"), 0);
});

test("waitForDashboardPort times out and removes listeners", async () => {
  const child = fakeChild();
  const promise = waitForDashboardPort(child, 5);

  await assert.rejects(promise, /Timed out waiting/);
  assert.equal(child.stdout.listenerCount("data"), 0);
  assert.equal(child.listenerCount("exit"), 0);
  assert.equal(child.listenerCount("error"), 0);
});
