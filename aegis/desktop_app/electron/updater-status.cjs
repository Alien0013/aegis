"use strict";

function _isoNow(now = () => new Date()) {
  const value = typeof now === "function" ? now() : now;
  const date = value instanceof Date ? value : new Date(value || Date.now());
  if (Number.isNaN(date.getTime())) return new Date().toISOString();
  return date.toISOString();
}

function _text(value) {
  return value == null ? "" : String(value);
}

function _version(details = {}) {
  return _text(details.version || (details.info && details.info.version) || "");
}

function _errorMessage(details = {}) {
  const error = details.error == null ? details : details.error;
  return _text(error && error.message ? error.message : error) || "Update check failed.";
}

function _number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
}

function _downloadProgress(details = {}) {
  const progress = details.progress && typeof details.progress === "object" ? details.progress : details;
  const percent = Math.max(0, Math.min(100, _number(progress.percent)));
  return {
    percent: Math.round(percent * 10) / 10,
    transferred: Math.floor(_number(progress.transferred)),
    total: Math.floor(_number(progress.total)),
    bytesPerSecond: Math.floor(_number(progress.bytesPerSecond)),
  };
}

function initialUpdaterStatus(now) {
  return {
    stage: "idle",
    message: "",
    error: "",
    version: "",
    checking: false,
    lastCheckedAt: "",
    downloadProgress: {
      percent: 0,
      transferred: 0,
      total: 0,
      bytesPerSecond: 0,
    },
    updatedAt: _isoNow(now),
  };
}

function transitionUpdaterStatus(current = {}, event = "idle", details = {}, now) {
  const at = _isoNow(now);
  const base = { ...initialUpdaterStatus(() => at), ...current };
  const stage = _text(event || "idle");
  const next = {
    ...base,
    stage,
    updatedAt: at,
  };

  if (stage === "checking") {
    return {
      ...next,
      message: _text(details.message) || "Checking for AEGIS updates...",
      error: "",
      checking: true,
      downloadProgress: initialUpdaterStatus(() => at).downloadProgress,
    };
  }

  if (stage === "disabled") {
    return {
      ...next,
      message: _text(details.message || details.reason) || "Auto-update is disabled.",
      error: "",
      checking: false,
      downloadProgress: initialUpdaterStatus(() => at).downloadProgress,
    };
  }

  if (stage === "available") {
    const version = _version(details);
    return {
      ...next,
      stage: "downloading",
      message: version ? `Downloading ${version}...` : "Downloading update...",
      error: "",
      version,
      checking: false,
      downloadProgress: initialUpdaterStatus(() => at).downloadProgress,
    };
  }

  if (stage === "progress" || stage === "download-progress") {
    const version = _version(details) || base.version;
    const progress = _downloadProgress(details);
    const label = progress.percent ? ` (${progress.percent}%)` : "";
    return {
      ...next,
      stage: "downloading",
      message: version ? `Downloading ${version}${label}...` : `Downloading update${label}...`,
      error: "",
      version,
      checking: false,
      downloadProgress: progress,
    };
  }

  if (stage === "current") {
    return {
      ...next,
      message: _text(details.message) || "You're on the latest version.",
      error: "",
      checking: false,
      lastCheckedAt: at,
      downloadProgress: initialUpdaterStatus(() => at).downloadProgress,
    };
  }

  if (stage === "ready") {
    const version = _version(details) || base.version;
    const progress = base.downloadProgress || initialUpdaterStatus(() => at).downloadProgress;
    return {
      ...next,
      message: version ? `AEGIS ${version} is ready to install.` : "Update is ready to install.",
      error: "",
      version,
      checking: false,
      lastCheckedAt: at,
      downloadProgress: {
        ...progress,
        percent: progress.total || progress.transferred ? 100 : progress.percent,
      },
    };
  }

  if (stage === "error") {
    const message = _errorMessage(details);
    return {
      ...next,
      message,
      error: message,
      checking: false,
      lastCheckedAt: at,
    };
  }

  return {
    ...next,
    message: _text(details.message) || base.message,
    error: _text(details.error) || base.error,
    checking: Boolean(details.checking),
  };
}

module.exports = {
  initialUpdaterStatus,
  transitionUpdaterStatus,
};
