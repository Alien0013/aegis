"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { runRebuildWithRetry, shouldRetryRebuild } = require("./update-rebuild.cjs");

test("shouldRetryRebuild retries only non-zero rebuild exits", () => {
  assert.equal(shouldRetryRebuild(0), false);
  assert.equal(shouldRetryRebuild(1), true);
  assert.equal(shouldRetryRebuild({ code: 0 }), false);
  assert.equal(shouldRetryRebuild({ code: 2 }), true);
});

test("runRebuildWithRetry retries once after a failed first rebuild", async () => {
  const attempts = [];
  const result = await runRebuildWithRetry(async (attempt) => {
    attempts.push(attempt);
    return { code: attempt === 0 ? 1 : 0, attempt };
  });
  assert.deepEqual(attempts, [0, 1]);
  assert.deepEqual(result, { code: 0, attempt: 1 });
});

test("runRebuildWithRetry does not retry a successful first rebuild", async () => {
  const attempts = [];
  const result = await runRebuildWithRetry(async (attempt) => {
    attempts.push(attempt);
    return { code: 0, attempt };
  });
  assert.deepEqual(attempts, [0]);
  assert.deepEqual(result, { code: 0, attempt: 0 });
});
