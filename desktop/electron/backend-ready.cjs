"use strict";

const READY_RE = /^AEGIS_DASHBOARD_READY port=(\d+)/m;

function _removeListener(target, event, listener) {
  if (!target || !listener) return;
  if (typeof target.off === "function") {
    target.off(event, listener);
  } else if (typeof target.removeListener === "function") {
    target.removeListener(event, listener);
  }
}

function waitForDashboardPort(child, timeoutMs = 45_000) {
  return new Promise((resolve, reject) => {
    if (!child || !child.stdout || typeof child.stdout.on !== "function") {
      reject(new Error("AEGIS backend child stdout is not readable"));
      return;
    }

    let buffer = "";
    let done = false;

    function cleanup() {
      if (done) return;
      done = true;
      clearTimeout(timer);
      _removeListener(child.stdout, "data", onData);
      _removeListener(child, "exit", onExit);
      _removeListener(child, "error", onError);
    }

    function onData(chunk) {
      buffer += chunk.toString();
      let newline;
      while ((newline = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        const match = line.match(READY_RE);
        if (match) {
          cleanup();
          resolve(parseInt(match[1], 10));
          return;
        }
      }
    }

    function onExit(code, signal) {
      cleanup();
      reject(new Error(`AEGIS backend exited before readiness announcement (${signal || code})`));
    }

    function onError(error) {
      cleanup();
      reject(error);
    }

    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`Timed out waiting for AEGIS backend readiness announcement (${timeoutMs}ms)`));
    }, Math.max(1, Number(timeoutMs) || 45_000));

    child.stdout.on("data", onData);
    child.on("exit", onExit);
    child.on("error", onError);
  });
}

module.exports = {
  READY_RE,
  waitForDashboardPort,
};
