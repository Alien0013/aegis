"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  bootstrapManifest,
  buildBootstrapCommand,
  cachedScriptPath,
  candidateInstallScripts,
  installScriptKind,
  installScriptName,
  resolveLocalInstallScript,
  runBootstrap,
} = require("./bootstrap-runner.cjs");

function tempRepo() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "aegis-bootstrap-runner-"));
}

test("install script names and kinds are platform-specific", () => {
  assert.equal(installScriptName("win32"), "install.ps1");
  assert.equal(installScriptName("linux"), "install.sh");
  assert.equal(installScriptKind("win32"), "powershell");
  assert.equal(installScriptKind("darwin"), "posix");
});

test("resolveLocalInstallScript prefers root installer before scripts fallback", () => {
  const root = tempRepo();
  fs.mkdirSync(path.join(root, "scripts"));
  fs.writeFileSync(path.join(root, "scripts", "install.sh"), "fallback");
  assert.equal(resolveLocalInstallScript(root, { platform: "linux" }), path.join(root, "scripts", "install.sh"));
  fs.writeFileSync(path.join(root, "install.sh"), "root");
  assert.equal(resolveLocalInstallScript(root, { platform: "linux" }), path.join(root, "install.sh"));
  assert.deepEqual(candidateInstallScripts(root, "linux").length, 2);
});

test("buildBootstrapCommand uses AEGIS noninteractive install environment", () => {
  const posix = buildBootstrapCommand("/tmp/install.sh", { platform: "linux", extraArgs: ["--core"] });
  assert.equal(posix.program, "bash");
  assert.deepEqual(posix.args, ["/tmp/install.sh", "--non-interactive", "--core"]);
  assert.equal(posix.env.AEGIS_NO_PROMPT, "1");
  assert.equal(posix.env.AEGIS_NONINTERACTIVE_ONBOARD, "1");

  const win = buildBootstrapCommand("C:/aegis/install.ps1", { platform: "win32" });
  assert.equal(win.program, "powershell.exe");
  assert.deepEqual(win.args.slice(0, 5), ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "C:/aegis/install.ps1"]);
});

test("cachedScriptPath and manifest use AEGIS product identity", () => {
  assert.equal(cachedScriptPath("/tmp/aegis", "abc/def", "linux"), "/tmp/aegis/bootstrap-cache/install-abc_def.sh");
  const manifest = bootstrapManifest("win32");
  assert.equal(manifest.product, "AEGIS");
  assert.equal(manifest.script, "install.ps1");
  assert.equal(manifest.kind, "powershell");
  assert.equal(manifest.stages.length, 3);
});

test("runBootstrap emits manifest, logs, and completion", () => {
  const root = tempRepo();
  fs.writeFileSync(path.join(root, "install.sh"), "#!/bin/sh\n");
  const events = [];
  const result = runBootstrap({
    sourceRepoRoot: root,
    platform: "linux",
    emit: (event) => events.push(event),
    spawnSync: (program, args, options) => {
      assert.equal(program, "bash");
      assert.equal(args[1], "--non-interactive");
      assert.equal(options.windowsHide, true);
      assert.equal(options.env.AEGIS_NO_PROMPT, "1");
      return { status: 0, stdout: "installed\n", stderr: "" };
    },
  });
  assert.equal(result.ok, true);
  assert.equal(events[0].type, "manifest");
  assert.equal(events.some((event) => event.type === "log" && event.line === "installed"), true);
  assert.equal(events.at(-1).type, "complete");
});

test("runBootstrap reports missing installer before spawning", () => {
  const events = [];
  const result = runBootstrap({
    sourceRepoRoot: tempRepo(),
    platform: "linux",
    emit: (event) => events.push(event),
    spawnSync: () => {
      throw new Error("should not spawn");
    },
  });
  assert.equal(result.ok, false);
  assert.equal(result.stage, "resolve-install-script");
  assert.equal(events.at(-1).type, "failed");
});
