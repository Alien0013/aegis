"use strict";

function _codeOf(resultOrCode) {
  if (typeof resultOrCode === "number") return resultOrCode;
  if (resultOrCode && typeof resultOrCode === "object") return Number(resultOrCode.code || 0);
  return 0;
}

function shouldRetryRebuild(resultOrCode) {
  return _codeOf(resultOrCode) !== 0;
}

async function runRebuildWithRetry(rebuild) {
  if (typeof rebuild !== "function") throw new TypeError("rebuild must be a function");
  let result = await rebuild(0);
  if (shouldRetryRebuild(result)) result = await rebuild(1);
  return result;
}

module.exports = { runRebuildWithRetry, shouldRetryRebuild };
