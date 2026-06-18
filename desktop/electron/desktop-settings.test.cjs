const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  desktopProjectCwd,
  readDesktopSettings,
  settingsPath,
  writeDesktopSettings,
} = require("./desktop-settings.cjs");

test("desktop settings persist a default project directory", () => {
  const userData = fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-settings-"));
  const project = path.join(userData, "project");
  fs.mkdirSync(project);

  assert.equal(settingsPath({ userData }), path.join(userData, "desktop-settings.json"));
  assert.deepEqual(readDesktopSettings({ userData }), { defaultProjectDir: "" });
  assert.deepEqual(writeDesktopSettings({ defaultProjectDir: ` ${project} ` }, { userData }), {
    defaultProjectDir: project,
  });
  assert.deepEqual(readDesktopSettings({ userData }), { defaultProjectDir: project });
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
      settings: { defaultProjectDir: project },
      explicitLaunchCwd: true,
    },
  );
  assert.deepEqual(
    desktopProjectCwd({ env: {}, userData, cwd: "/fallback" }),
    {
      cwd: project,
      source: "desktop-settings",
      settings: { defaultProjectDir: project },
      explicitLaunchCwd: false,
    },
  );
  assert.deepEqual(
    desktopProjectCwd({ env: {}, userData, cwd: "/fallback", exists: () => false }),
    {
      cwd: "/fallback",
      source: "process",
      settings: { defaultProjectDir: project },
      explicitLaunchCwd: false,
    },
  );
});
