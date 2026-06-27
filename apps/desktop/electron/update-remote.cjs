"use strict";

const OFFICIAL_REPO_HTTPS_URL = "https://github.com/Alien0013/aegis.git";
const OFFICIAL_REPO_CANONICAL = "github.com/alien0013/aegis";

function canonicalGitHubRemote(url) {
  let value = String(url || "").trim();
  if (!value) return "";
  if (value.startsWith("git@github.com:")) {
    value = `github.com/${value.slice("git@github.com:".length)}`;
  } else if (value.startsWith("ssh://git@github.com/")) {
    value = `github.com/${value.slice("ssh://git@github.com/".length)}`;
  } else {
    try {
      const parsed = new URL(value);
      if (parsed.hostname && parsed.pathname) value = `${parsed.hostname}${parsed.pathname}`;
    } catch {
      // Non-URL remotes are normalized below as-is.
    }
  }
  value = value.trim().replace(/\/+$/, "");
  if (value.endsWith(".git")) value = value.slice(0, -4);
  return value.toLowerCase();
}

function isSshRemote(url) {
  const value = String(url || "").trim().toLowerCase();
  return value.startsWith("git@") || value.startsWith("ssh://");
}

function isOfficialSshRemote(url) {
  return isSshRemote(url) && canonicalGitHubRemote(url) === OFFICIAL_REPO_CANONICAL;
}

module.exports = {
  OFFICIAL_REPO_CANONICAL,
  OFFICIAL_REPO_HTTPS_URL,
  canonicalGitHubRemote,
  isOfficialSshRemote,
  isSshRemote,
};
