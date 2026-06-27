"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { resolveBehindCount, shouldCountCommits } = require("./update-count.cjs");

test("shallow clones without a merge base skip expensive commit counting", () => {
  assert.equal(shouldCountCommits({ isShallow: true, hasMergeBase: false }), false);
  assert.equal(shouldCountCommits({ isShallow: true, hasMergeBase: true }), true);
  assert.equal(shouldCountCommits({ isShallow: false, hasMergeBase: false }), true);
});

test("skipped shallow count falls back to binary sha comparison", () => {
  assert.equal(
    resolveBehindCount({
      countStr: "12104",
      currentSha: "aaa",
      targetSha: "bbb",
      isShallow: true,
      hasMergeBase: false,
    }),
    1,
  );
  assert.equal(
    resolveBehindCount({
      countStr: "12104",
      currentSha: "same",
      targetSha: "same",
      isShallow: true,
      hasMergeBase: false,
    }),
    0,
  );
});

test("reliable count paths keep the parsed count", () => {
  assert.equal(resolveBehindCount({ countStr: "3", isShallow: true, hasMergeBase: true }), 3);
  assert.equal(resolveBehindCount({ countStr: "7", isShallow: false, hasMergeBase: false }), 7);
  assert.equal(resolveBehindCount({ countStr: "", isShallow: false, hasMergeBase: true }), 0);
});
