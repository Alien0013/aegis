"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  OFFICIAL_REPO_CANONICAL,
  OFFICIAL_REPO_HTTPS_URL,
  canonicalGitHubRemote,
  isOfficialSshRemote,
  isSshRemote,
} = require("./update-remote.cjs");

test("canonicalGitHubRemote normalizes common AEGIS GitHub remote forms", () => {
  assert.equal(canonicalGitHubRemote("git@github.com:Alien0013/aegis.git"), OFFICIAL_REPO_CANONICAL);
  assert.equal(canonicalGitHubRemote("ssh://git@github.com/Alien0013/aegis.git"), OFFICIAL_REPO_CANONICAL);
  assert.equal(canonicalGitHubRemote("https://github.com/Alien0013/aegis.git"), OFFICIAL_REPO_CANONICAL);
  assert.equal(canonicalGitHubRemote("https://github.com/Alien0013/aegis/"), OFFICIAL_REPO_CANONICAL);
});

test("isOfficialSshRemote only matches the official AEGIS SSH remote", () => {
  assert.equal(OFFICIAL_REPO_HTTPS_URL, "https://github.com/Alien0013/aegis.git");
  assert.equal(isOfficialSshRemote("git@github.com:Alien0013/aegis.git"), true);
  assert.equal(isOfficialSshRemote("ssh://git@github.com/Alien0013/aegis.git"), true);
  assert.equal(isOfficialSshRemote("https://github.com/Alien0013/aegis.git"), false);
  assert.equal(isOfficialSshRemote("git@github.com:someone/else.git"), false);
});

test("isSshRemote detects ssh remotes without treating https as ssh", () => {
  assert.equal(isSshRemote("git@github.com:Alien0013/aegis.git"), true);
  assert.equal(isSshRemote("ssh://git@github.com/Alien0013/aegis.git"), true);
  assert.equal(isSshRemote("https://github.com/Alien0013/aegis.git"), false);
});
