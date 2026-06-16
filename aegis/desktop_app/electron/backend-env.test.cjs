const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const {
  aegisCommand,
  backendEnvironment,
  candidateAegisCommands,
  normalizePathEnv,
  resolveAegisHome,
} = require("./backend-env.cjs");

test("prefers explicit AEGIS_BIN when it exists", () => {
  const env = { AEGIS_BIN: "/opt/aegis/bin/aegis" };
  assert.equal(
    aegisCommand({
      env,
      exists: (p) => p === env.AEGIS_BIN,
      probeCommand: (p) => p === env.AEGIS_BIN,
    }),
    env.AEGIS_BIN,
  );
});

test("uses Unix AEGIS_HOME venv before PATH fallback", () => {
  const env = { AEGIS_HOME: "/srv/aegis" };
  const expected = path.posix.join(env.AEGIS_HOME, "venv", "bin", "aegis");
  assert.equal(
    aegisCommand({
      platform: "linux",
      env,
      homedir: "/home/alien",
      exists: (p) => p === expected,
      probeCommand: (p) => p === expected,
    }),
    expected,
  );
});

test("reads Windows user env for stale Explorer-launched process env", () => {
  const registry = {
    AEGIS_HOME: "D:\\AegisHome",
    AEGIS_BIN: "D:\\Tools\\aegis.exe",
  };
  const readUserEnvVar = (name) => registry[name] || null;
  const env = backendEnvironment({}, { platform: "win32", cwd: "C:\\work", readUserEnvVar });

  assert.equal(env.AEGIS_HOME, registry.AEGIS_HOME);
  assert.equal(env.AEGIS_BIN, registry.AEGIS_BIN);
  assert.equal(env.TERMINAL_CWD, "C:\\work");
  assert.equal(
    aegisCommand({
      platform: "win32",
      env,
      homedir: "C:\\Users\\Alien",
      exists: (p) => p === registry.AEGIS_BIN,
      probeCommand: (p) => p === registry.AEGIS_BIN,
    }),
    registry.AEGIS_BIN,
  );
});

test("falls back to LocalAppData Windows venv candidates", () => {
  const env = { LOCALAPPDATA: "C:\\Users\\Alien\\AppData\\Local" };
  const home = resolveAegisHome({ platform: "win32", env, homedir: "C:\\Users\\Alien" });
  const expected = path.win32.join(home, "venv", "Scripts", "aegis.exe");
  assert.equal(home, "C:\\Users\\Alien\\AppData\\Local\\aegis");
  assert.equal(candidateAegisCommands({ platform: "win32", env, homedir: "C:\\Users\\Alien" })[0], expected);
  assert.equal(
    aegisCommand({
      platform: "win32",
      env,
      homedir: "C:\\Users\\Alien",
      exists: (p) => p === expected,
      probeCommand: (p) => p === expected,
    }),
    expected,
  );
});

test("skips stale executable candidates when version probe fails", () => {
  const env = { AEGIS_BIN: "/old/aegis", AEGIS_HOME: "/srv/aegis" };
  const good = path.posix.join(env.AEGIS_HOME, "venv", "bin", "aegis");
  assert.equal(
    aegisCommand({
      platform: "linux",
      env,
      exists: (p) => p === env.AEGIS_BIN || p === good,
      probeCommand: (p) => p === good,
    }),
    good,
  );
});

test("normalizes PATH for GUI-launched POSIX desktop processes", () => {
  const env = normalizePathEnv({ PATH: "/custom/bin:/usr/bin" }, { platform: "darwin" });
  const entries = env.PATH.split(":");
  assert.equal(entries[0], "/opt/homebrew/bin");
  assert(entries.includes("/usr/local/bin"));
  assert.equal(entries.filter((entry) => entry === "/usr/bin").length, 1);
});

test("preserves Windows Path casing while merging PATH-like entries", () => {
  const env = normalizePathEnv({ Path: "C:\\Tools" }, { platform: "win32" });
  assert.equal(env.Path, "C:\\Tools");
  assert.equal(env.PATH, undefined);
});

test("rejects Windows binaries when resolving inside WSL", () => {
  const bad = "/mnt/c/Users/Alien/AppData/Local/aegis/venv/Scripts/aegis.exe";
  const good = "/home/alien/.aegis/venv/bin/aegis";
  assert.equal(
    aegisCommand({
      platform: "linux",
      env: { AEGIS_BIN: bad, WSL_DISTRO_NAME: "Ubuntu" },
      homedir: "/home/alien",
      exists: (p) => p === bad || p === good,
      probeCommand: (p) => p === bad || p === good,
    }),
    good,
  );
});
