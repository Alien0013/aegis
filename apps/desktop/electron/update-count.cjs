"use strict";

function shouldCountCommits({ isShallow, hasMergeBase } = {}) {
  return !(Boolean(isShallow) && !Boolean(hasMergeBase));
}

function resolveBehindCount({
  countStr = "",
  currentSha = "",
  targetSha = "",
  isShallow = false,
  hasMergeBase = true,
} = {}) {
  if (!shouldCountCommits({ isShallow, hasMergeBase })) {
    const current = String(currentSha || "").trim();
    const target = String(targetSha || "").trim();
    return current && target && current === target ? 0 : 1;
  }
  const parsed = Number.parseInt(String(countStr || ""), 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

module.exports = { resolveBehindCount, shouldCountCommits };
