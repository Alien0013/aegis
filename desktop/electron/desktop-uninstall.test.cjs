"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  candidateDesktopUninstallScripts,
  desktopUninstallPlan,
} = require("./desktop-uninstall.cjs");
const { desktopDiagnostics } = require("./desktop-status.cjs");

function tmpRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aegis-desktop-uninstall-"));
}

test("desktop uninstall helper prefers packaged resource script before source checkout", () => {
  const root = tmpRoot();
  const desktopRoot = path.join(root, "desktop");
  const resourcesPath = path.join(root, "resources");
  fs.mkdirSync(desktopRoot, { recursive: true });
  fs.mkdirSync(resourcesPath, { recursive: true });
  fs.writeFileSync(path.join(root, "uninstall.sh"), "#!/usr/bin/env bash\n");
  fs.writeFileSync(path.join(resourcesPath, "uninstall.sh"), "#!/usr/bin/env bash\n");

  assert.deepEqual(candidateDesktopUninstallScripts({ desktopRoot, resourcesPath }), [
    path.join(resourcesPath, "uninstall.sh"),
    path.join(root, "uninstall.sh"),
  ]);

  const plan = desktopUninstallPlan({ desktopRoot, resourcesPath, platform: "linux", purge: true });
  assert.equal(plan.available, true);
  assert.equal(plan.command, "bash");
  assert.deepEqual(plan.args, [path.join(resourcesPath, "uninstall.sh"), "--purge"]);
});

test("desktop uninstall helper is disabled on Windows packaged builds", () => {
  const plan = desktopUninstallPlan({ platform: "win32" });
  assert.equal(plan.available, false);
  assert.match(plan.reason, /Windows app uninstaller/);
  assert.equal(plan.command, "");
});

test("desktop diagnostics exposes uninstall action only when a script is available", () => {
  const root = tmpRoot();
  const resourcesPath = path.join(root, "resources");
  fs.mkdirSync(resourcesPath, { recursive: true });
  fs.writeFileSync(path.join(resourcesPath, "uninstall.sh"), "#!/usr/bin/env bash\n");

  const report = desktopDiagnostics({
    app: { isPackaged: true, getVersion: () => "1.0.0", getPath: () => root, getAppPath: () => root },
    desktopRoot: path.join(root, "desktop"),
    resourcesPath,
    platform: "linux",
    exists: fs.existsSync,
    probeCommand: () => false,
  });

  const action = report.repair.actions.find((row) => row.id === "uninstall_app");
  assert.equal(action.disabled, false);
  assert.match(action.description, /native uninstall script/);
});
