"use strict";

const DEFAULT_TOKEN_FETCH_TIMEOUT_MS = 3000;
const TOKEN_ASSIGNMENT = /window\.__AEGIS_SESSION_TOKEN__\s*=\s*("(?:\\.|[^"\\])*")/;

async function fetchPublicText(url, options = {}) {
  const parsed = new URL(String(url || ""));
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`Unsupported AEGIS backend URL protocol: ${parsed.protocol}`);
  }

  const timeoutMs = options.timeoutMs ?? DEFAULT_TOKEN_FETCH_TIMEOUT_MS;
  const signal = options.signal || AbortSignal.timeout(timeoutMs);
  let response;
  try {
    response = await fetch(parsed.toString(), { signal });
  } catch (error) {
    if (error && error.name === "TimeoutError") {
      throw new Error(`Timed out connecting to AEGIS backend after ${timeoutMs}ms`);
    }
    throw error;
  }

  const text = await response.text();
  if (!response.ok) throw new Error(`${response.status}: ${text || response.statusText}`);
  return text;
}

function extractInjectedDashboardToken(html) {
  const match = TOKEN_ASSIGNMENT.exec(String(html || ""));
  if (!match) return null;
  try {
    const parsed = JSON.parse(match[1]);
    return typeof parsed === "string" ? parsed : null;
  } catch {
    return null;
  }
}

function dashboardIndexUrl(baseUrl) {
  return `${String(baseUrl || "").replace(/\/+$/, "")}/`;
}

async function resolveServedDashboardToken(baseUrl, fallbackToken, options = {}) {
  const fetchText = options.fetchText || fetchPublicText;
  const html = await fetchText(dashboardIndexUrl(baseUrl), {
    timeoutMs: options.timeoutMs ?? DEFAULT_TOKEN_FETCH_TIMEOUT_MS,
  });
  const servedToken = extractInjectedDashboardToken(html);
  if (servedToken && servedToken !== fallbackToken && typeof options.rememberLog === "function") {
    options.rememberLog("[boot] dashboard served a different AEGIS session token; using served token for desktop auth");
  }
  return servedToken || fallbackToken;
}

function isForeignBackendToken({ servedToken, spawnToken, childAlive }) {
  return Boolean(servedToken) && servedToken !== spawnToken && !childAlive;
}

async function adoptServedDashboardToken(baseUrl, spawnToken, options = {}) {
  const label = options.label || "AEGIS backend";
  const fetchText = options.fetchText || fetchPublicText;
  const html = await fetchText(dashboardIndexUrl(baseUrl), {
    timeoutMs: options.timeoutMs ?? DEFAULT_TOKEN_FETCH_TIMEOUT_MS,
  }).catch((error) => {
    if (typeof options.rememberLog === "function") {
      options.rememberLog(`[boot] could not read served AEGIS session token (${label}): ${error.message}`);
    }
    return "";
  });
  if (!html) return spawnToken;

  const servedToken = extractInjectedDashboardToken(html);
  if (servedToken && servedToken !== spawnToken && typeof options.rememberLog === "function") {
    options.rememberLog("[boot] dashboard served a different AEGIS session token; using served token for desktop auth");
  }
  const childAlive = typeof options.childAlive === "function" ? Boolean(options.childAlive()) : true;
  if (isForeignBackendToken({ servedToken, spawnToken, childAlive })) {
    throw new Error(`${label} exited and ${dashboardIndexUrl(baseUrl)} is served by a process we did not spawn; refusing its session token.`);
  }
  return servedToken || spawnToken;
}

module.exports = {
  DEFAULT_TOKEN_FETCH_TIMEOUT_MS,
  adoptServedDashboardToken,
  dashboardIndexUrl,
  extractInjectedDashboardToken,
  fetchPublicText,
  isForeignBackendToken,
  resolveServedDashboardToken,
};
