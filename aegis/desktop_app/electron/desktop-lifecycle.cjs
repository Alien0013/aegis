"use strict";

const VALID_STATES = new Set([
  "booting",
  "probing_backend",
  "ready",
  "remote_mode",
  "updating",
  "crashed",
  "repairing",
  "stopped",
]);

const DEFAULT_MESSAGES = {
  booting: "Starting AEGIS desktop.",
  probing_backend: "Waiting for the backend to become ready.",
  ready: "AEGIS desktop is ready.",
  remote_mode: "Connected to a remote dashboard.",
  updating: "Desktop update is in progress.",
  crashed: "The backend stopped unexpectedly.",
  repairing: "Repair action is running.",
  stopped: "Desktop backend is stopped.",
};

function _isoNow(now = new Date()) {
  const date = now instanceof Date ? now : new Date(now);
  return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
}

function _state(value) {
  const normalized = String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
  return VALID_STATES.has(normalized) ? normalized : "booting";
}

function _text(value, fallback = "") {
  return String(value || fallback || "").slice(0, 400);
}

function _cap(items, limit) {
  return items.slice(Math.max(0, items.length - limit));
}

function initialDesktopLifecycle(now = new Date()) {
  return transitionDesktopLifecycle(
    { state: "", events: [], crashHistory: [] },
    "booting",
    { message: DEFAULT_MESSAGES.booting },
    now,
  );
}

function transitionDesktopLifecycle(current = {}, nextState = "booting", details = {}, now = new Date()) {
  const state = _state(nextState);
  const at = _isoNow(now);
  const message = _text(details.message, DEFAULT_MESSAGES[state]);
  const event = {
    state,
    at,
    message,
    phase: _text(details.phase),
    mode: _text(details.mode),
    backendPid: details.backendPid || null,
    port: Number(details.port || 0),
    restartAttempt: Number(details.restartAttempt || 0),
    updateStage: _text(details.updateStage),
  };
  const events = _cap([...(Array.isArray(current.events) ? current.events : []), event], 40);
  const previousCrashes = Array.isArray(current.crashHistory) ? current.crashHistory : [];
  const crashHistory = state === "crashed"
    ? _cap([
        ...previousCrashes,
        {
          at,
          message,
          code: details.code ?? null,
          signal: details.signal || "",
          restartAttempt: event.restartAttempt,
          maxCrashRestarts: Number(details.maxCrashRestarts || 0),
        },
      ], 20)
    : previousCrashes.slice(-20);
  return {
    state,
    message,
    phase: event.phase,
    mode: event.mode,
    backendPid: event.backendPid,
    port: event.port,
    restartAttempt: event.restartAttempt,
    updateStage: event.updateStage,
    updatedAt: at,
    events,
    crashHistory,
  };
}

function lifecyclePublicSnapshot(lifecycle = {}) {
  const state = _state(lifecycle.state);
  return {
    state,
    message: _text(lifecycle.message, DEFAULT_MESSAGES[state]),
    phase: _text(lifecycle.phase),
    mode: _text(lifecycle.mode),
    backendPid: lifecycle.backendPid || null,
    port: Number(lifecycle.port || 0),
    restartAttempt: Number(lifecycle.restartAttempt || 0),
    updateStage: _text(lifecycle.updateStage),
    updatedAt: _text(lifecycle.updatedAt),
    events: Array.isArray(lifecycle.events) ? lifecycle.events.slice(-12) : [],
    crashHistory: Array.isArray(lifecycle.crashHistory) ? lifecycle.crashHistory.slice(-8) : [],
  };
}

module.exports = {
  DEFAULT_MESSAGES,
  VALID_STATES,
  initialDesktopLifecycle,
  lifecyclePublicSnapshot,
  transitionDesktopLifecycle,
};
