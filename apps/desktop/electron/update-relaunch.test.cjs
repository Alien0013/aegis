"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const {
  buildRelaunchScript,
  collectRelaunchArgs,
  collectRelaunchEnv,
  decideRelaunchOutcome,
  resolveUnpackedRelease,
  sandboxFallbackFromEnv,
  sandboxPreflight,
  unpackedDirName,
} = require("./update-relaunch.cjs");

test("resolveUnpackedRelease accepts only the rebuilt unpacked desktop tree", () => {
  const root = path.join("/tmp", "aegis-update");
  const unpacked = path.join(root, "apps", "desktop", "release", "linux-unpacked");
  assert.equal(unpackedDirName("linux"), "linux-unpacked");
  assert.equal(resolveUnpackedRelease(path.join(unpacked, "AEGIS"), root, "linux"), unpacked);
  assert.equal(resolveUnpackedRelease(`${unpacked}-evil/AEGIS`, root, "linux"), null);
  assert.equal(resolveUnpackedRelease("/opt/AEGIS/AEGIS", root, "linux"), null);
});

test("relaunch outcome distinguishes GUI skew, sandbox manual, and relaunch", () => {
  assert.equal(decideRelaunchOutcome({ underUnpacked: false, sandboxOk: true }), "guiSkew");
  assert.equal(decideRelaunchOutcome({ underUnpacked: true, sandboxOk: false }), "manual");
  assert.equal(decideRelaunchOutcome({ underUnpacked: true, sandboxOk: false, sandboxFallback: true }), "relaunch");
  assert.equal(decideRelaunchOutcome({ underUnpacked: true, sandboxOk: true }), "relaunch");
});

test("sandboxPreflight validates chrome-sandbox ownership and setuid", () => {
  assert.deepEqual(sandboxPreflight(null, () => ({})), { ok: false, reason: "no-unpacked-dir", path: null });
  assert.equal(sandboxPreflight("/tmp/release", () => { throw new Error("missing"); }).ok, true);
  assert.equal(sandboxPreflight("/tmp/release", () => ({ uid: 0, mode: 0o4755 })).ok, true);
  assert.equal(sandboxPreflight("/tmp/release", () => ({ uid: 1000, mode: 0o0755 })).reason, "not-root-not-setuid");
  assert.equal(sandboxPreflight("/tmp/release", () => ({ uid: 1000, mode: 0o4755 })).reason, "not-root");
  assert.equal(sandboxPreflight("/tmp/release", () => ({ uid: 0, mode: 0o0755 })).reason, "not-setuid");
});

test("relaunch context keeps app intent and AEGIS environment only", () => {
  assert.deepEqual(
    collectRelaunchArgs(["aegis://open", "--type=renderer", "--no-sandbox", "--lang=en", "--custom"]),
    ["aegis://open", "--no-sandbox", "--custom"],
  );
  assert.equal(sandboxFallbackFromEnv({}, ["--no-sandbox"]), true);
  assert.equal(sandboxFallbackFromEnv({ ELECTRON_DISABLE_SANDBOX: "true" }, []), true);
  assert.deepEqual(
    collectRelaunchEnv({ AEGIS_HOME: "/tmp/aegis", AEGIS_DESKTOP_MODE: "remote", PATH: "/bin" }),
    { AEGIS_HOME: "/tmp/aegis", AEGIS_DESKTOP_MODE: "remote" },
  );
});

test("buildRelaunchScript quotes exec path, args, env, and cwd", () => {
  const script = buildRelaunchScript({
    pid: 123,
    execPath: "/opt/AEGIS App/AEGIS",
    args: ["aegis://open", "needs'quote"],
    env: { AEGIS_HOME: "/tmp/aegis home" },
    cwd: "/tmp/aegis work",
  });
  assert.match(script, /APP_PID=123/);
  assert.match(script, /export AEGIS_HOME='\/tmp\/aegis home'/);
  assert.match(script, /cd '\/tmp\/aegis work'/);
  assert.match(script, /exec '\/opt\/AEGIS App\/AEGIS' 'aegis:\/\/open' 'needs'\\''quote'/);
});
