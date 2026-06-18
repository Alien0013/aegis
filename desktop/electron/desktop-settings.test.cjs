const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  desktopRemoteConnection,
  desktopProjectCwd,
  normalizeRemoteUrl,
  readDesktopSettings,
  settingsPath,
  writeDesktopSettings,
} = require("./desktop-settings.cjs");

test("desktop settings persist a default project directory", () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-settings-"));
  const project = path.join(userData, "project");
  fs.mkdirSync(project);

  assert.equal(settingsPath({ userData }), path.join(userData, "desktop-settings.json"));
  assert.deepEqual(readDesktopSettings({ userData }), {
    defaultProjectDir: "",
    backendEnv: { AEGIS_HOME: "", AEGIS_BIN: "" },
    remoteBackend: { url: "", token: "" },
  });
  assert.deepEqual(writeDesktopSettings({ defaultProjectDir: ` ${project} ` }, { userData }), {
    defaultProjectDir: project,
    backendEnv: { AEGIS_HOME: "", AEGIS_BIN: "" },
    remoteBackend: { url: "", token: "" },
  });
  assert.deepEqual(readDesktopSettings({ userData }), {
    defaultProjectDir: project,
    backendEnv: { AEGIS_HOME: "", AEGIS_BIN: "" },
    remoteBackend: { url: "", token: "" },
  });

  assert.deepEqual(
    writeDesktopSettings({ backendEnv: { AEGIS_HOME: ` ${userData} `, AEGIS_BIN: "/bin/aegis" } }, { userData }),
    {
      defaultProjectDir: project,
      backendEnv: { AEGIS_HOME: userData, AEGIS_BIN: "/bin/aegis" },
      remoteBackend: { url: "", token: "" },
    },
  );
});

test("desktop project cwd prefers explicit launch env, then persisted setting", () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-settings-"));
  const project = path.join(userData, "project");
  fs.mkdirSync(project);
  writeDesktopSettings({ defaultProjectDir: project }, { userData });

  assert.deepEqual(
    desktopProjectCwd({ env: { TERMINAL_CWD: "/explicit" }, userData, cwd: "/fallback" }),
    {
      cwd: "/explicit",
      source: "env",
      settings: {
        defaultProjectDir: project,
        backendEnv: { AEGIS_HOME: "", AEGIS_BIN: "" },
        remoteBackend: { url: "", token: "" },
      },
      explicitLaunchCwd: true,
    },
  );
  assert.deepEqual(
    desktopProjectCwd({ env: {}, userData, cwd: "/fallback" }),
    {
      cwd: project,
      source: "desktop-settings",
      settings: {
        defaultProjectDir: project,
        backendEnv: { AEGIS_HOME: "", AEGIS_BIN: "" },
        remoteBackend: { url: "", token: "" },
      },
      explicitLaunchCwd: false,
    },
  );
  assert.deepEqual(
    desktopProjectCwd({ env: {}, userData, cwd: "/fallback", exists: () => false }),
    {
      cwd: "/fallback",
      source: "process",
      settings: {
        defaultProjectDir: project,
        backendEnv: { AEGIS_HOME: "", AEGIS_BIN: "" },
        remoteBackend: { url: "", token: "" },
      },
      explicitLaunchCwd: false,
    },
  );
});

test("desktop remote connection is normalized from settings and env", () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-settings-"));

  assert.equal(normalizeRemoteUrl("file:///tmp/aegis"), "");
  assert.equal(normalizeRemoteUrl(" https://agent.example.test:8443/ "), "https://agent.example.test:8443");

  assert.deepEqual(
    writeDesktopSettings({
      remoteBackend: {
        url: " https://agent.example.test/ ",
        token: " remote-token ",
      },
    }, { userData }),
    {
      defaultProjectDir: "",
      backendEnv: { AEGIS_HOME: "", AEGIS_BIN: "" },
      remoteBackend: { url: "https://agent.example.test", token: "remote-token" },
    },
  );
  assert.deepEqual(desktopRemoteConnection({ env: {}, userData }), {
    enabled: true,
    url: "https://agent.example.test",
    token: "remote-token",
    tokenConfigured: true,
    source: "desktop-settings",
    settings: {
      defaultProjectDir: "",
      backendEnv: { AEGIS_HOME: "", AEGIS_BIN: "" },
      remoteBackend: { url: "https://agent.example.test", token: "remote-token" },
    },
  });
  assert.deepEqual(
    desktopRemoteConnection({
      env: {
        AEGIS_DESKTOP_REMOTE_URL: "http://127.0.0.1:8810/",
        AEGIS_DESKTOP_REMOTE_TOKEN: "env-token",
      },
      userData,
    }),
    {
      enabled: true,
      url: "http://127.0.0.1:8810",
      token: "env-token",
      tokenConfigured: true,
      source: "env",
      settings: {
        defaultProjectDir: "",
        backendEnv: { AEGIS_HOME: "", AEGIS_BIN: "" },
        remoteBackend: { url: "https://agent.example.test", token: "remote-token" },
      },
    },
  );
});
