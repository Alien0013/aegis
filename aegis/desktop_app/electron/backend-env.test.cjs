const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const {
  aegisCommand,
  backendEnvironment,
  candidateAegisCommands,
  candidatePackagedAegisCommands,
  hiddenWindowsChildOptions,
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

test("prefers packaged resource backend before user install fallbacks", () => {
  const resourcesPath = "/opt/AEGIS/resources";
  const bundled = path.posix.join(resourcesPath, "aegis", "bin", "aegis");
  const home = "/home/alien/.aegis";
  const installed = path.posix.join(home, "venv", "bin", "aegis");

  assert.equal(
    aegisCommand({
      platform: "linux",
      packaged: true,
      resourcesPath,
      env: { AEGIS_HOME: home },
      exists: (p) => p === bundled || p === installed,
      probeCommand: (p) => p === bundled || p === installed,
    }),
    bundled,
  );
});

test("enumerates common packaged backend executable layouts", () => {
  const resourcesPath = "C:\\Program Files\\AEGIS\\resources";
  const candidates = candidatePackagedAegisCommands({ platform: "win32", resourcesPath });

  assert(candidates.includes(path.win32.join(resourcesPath, "aegis", "Scripts", "aegis.exe")));
  assert(candidates.includes(path.win32.join(resourcesPath, "backend", "Scripts", "aegis.cmd")));
  assert(candidates.includes(path.win32.join(resourcesPath, "venv", "Scripts", "aegis.bat")));
  assert(candidates.includes(path.win32.join(resourcesPath, "app.asar.unpacked", "aegis", "Scripts", "aegis.exe")));
});

test("reads Windows user env for stale Explorer-launched process env", () => {
  const registry = {
    AEGIS_HOME: "D:\\AegisHome",
    AEGIS_BIN: "D:\\Tools\\aegis.exe",
    Path: "D:\\Tools;C:\\Windows\\System32",
  };
  const readUserEnvVar = (name) => registry[name] || null;
  const env = backendEnvironment({}, { platform: "win32", cwd: "C:\\work", readUserEnvVar });

  assert.equal(env.AEGIS_HOME, registry.AEGIS_HOME);
  assert.equal(env.AEGIS_BIN, registry.AEGIS_BIN);
  assert.equal(env.Path, registry.Path);
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

test("merges live Windows user Path with stale process Path", () => {
  const env = backendEnvironment(
    { Path: "C:\\Old;C:\\Windows\\System32" },
    {
      platform: "win32",
      readUserEnvVar: (name) => (name === "Path" ? "D:\\Aegis\\bin;C:\\Windows\\System32" : null),
    },
  );
  assert.deepEqual(env.Path.split(";"), ["C:\\Old", "C:\\Windows\\System32", "D:\\Aegis\\bin"]);
});

test("hides Windows child processes unless a caller opts out", () => {
  assert.deepEqual(hiddenWindowsChildOptions({ stdio: "ignore" }, { platform: "win32" }), {
    stdio: "ignore",
    windowsHide: true,
  });
  assert.deepEqual(hiddenWindowsChildOptions({ windowsHide: false }, { platform: "win32" }), {
    windowsHide: false,
  });
  assert.deepEqual(hiddenWindowsChildOptions({}, { platform: "linux" }), {});
});

test("rejects Windows binaries when resolving inside WSL", () => {
  const bad = "/mnt/c/Users/Alien/AppData/Local/aegis/venv/Scripts/aegis.bat";
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
