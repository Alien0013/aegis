"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync: defaultSpawnSync } = require("node:child_process");

function installScriptName(platform = process.platform) {
  return platform === "win32" ? "install.ps1" : "install.sh";
}

function installScriptKind(platform = process.platform) {
  return platform === "win32" ? "powershell" : "posix";
}

function _unique(items) {
  return Array.from(new Set(items.filter(Boolean).map((item) => path.resolve(item))));
}

function candidateInstallScripts(sourceRepoRoot, platform = process.platform) {
  if (!sourceRepoRoot) return [];
  const name = installScriptName(platform);
  return _unique([
    path.join(sourceRepoRoot, name),
    path.join(sourceRepoRoot, "scripts", name),
  ]);
}

function resolveLocalInstallScript(sourceRepoRoot, options = {}) {
  const exists = options.exists || fs.existsSync;
  for (const candidate of candidateInstallScripts(sourceRepoRoot, options.platform)) {
    try {
      if (exists(candidate)) return candidate;
    } catch {
      // Ignore inaccessible candidates.
    }
  }
  return null;
}

function bootstrapCacheDir(aegisHome) {
  return path.join(aegisHome || process.cwd(), "bootstrap-cache");
}

function cachedScriptPath(aegisHome, commit, platform = process.platform) {
  const ext = platform === "win32" ? "ps1" : "sh";
  const safeCommit = String(commit || "unknown").replace(/[^0-9a-z_.-]/gi, "_");
  return path.join(bootstrapCacheDir(aegisHome), `install-${safeCommit}.${ext}`);
}

function buildBootstrapCommand(scriptPath, options = {}) {
  const platform = options.platform || process.platform;
  const extraArgs = Array.isArray(options.extraArgs) ? options.extraArgs : [];
  const env = {
    AEGIS_NO_PROMPT: "1",
    AEGIS_NONINTERACTIVE_ONBOARD: "1",
    ...(options.env || {}),
  };
  if (platform === "win32") {
    return {
      program: options.powershell || "powershell.exe",
      args: ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", scriptPath, ...extraArgs],
      env,
      kind: "powershell",
    };
  }
  return {
    program: options.shell || "bash",
    args: [scriptPath, "--non-interactive", ...extraArgs],
    env,
    kind: "posix",
  };
}

function bootstrapManifest(platform = process.platform) {
  return {
    product: "AEGIS",
    platform,
    script: installScriptName(platform),
    kind: installScriptKind(platform),
    stages: [
      { name: "resolve-install-script", title: "Resolve AEGIS installer", needs_user_input: false },
      { name: "run-installer", title: "Run AEGIS installer", needs_user_input: false },
      { name: "verify-command", title: "Verify aegis command", needs_user_input: false },
    ],
  };
}

function _emitLines(emit, stage, stream, text) {
  for (const line of String(text || "").split(/\r?\n/).filter(Boolean)) {
    emit({ type: "log", stage, stream, line });
  }
}

function runBootstrap(options = {}) {
  const emit = typeof options.emit === "function" ? options.emit : () => undefined;
  const platform = options.platform || process.platform;
  emit({ type: "manifest", ...bootstrapManifest(platform) });

  const script = options.scriptPath || resolveLocalInstallScript(options.sourceRepoRoot, { platform });
  if (!script) {
    const failed = { type: "failed", stage: "resolve-install-script", error: "AEGIS installer script not found" };
    emit(failed);
    return { ok: false, error: failed.error, stage: failed.stage };
  }

  const command = buildBootstrapCommand(script, {
    platform,
    env: options.installEnv,
    extraArgs: options.extraArgs,
  });
  emit({ type: "stage", name: "run-installer", state: "running", command });
  const spawnSync = options.spawnSync || defaultSpawnSync;
  const result = spawnSync(command.program, command.args, {
    encoding: "utf8",
    env: { ...process.env, ...command.env, ...(options.env || {}) },
    windowsHide: true,
  });
  _emitLines(emit, "run-installer", "stdout", result.stdout);
  _emitLines(emit, "run-installer", "stderr", result.stderr);

  if (result.status === 0) {
    const complete = { type: "complete", stage: "run-installer", status: 0 };
    emit(complete);
    return { ok: true, status: 0, command };
  }
  const error = result.error ? String(result.error.message || result.error) : String(result.stderr || "install failed").trim();
  const failed = { type: "failed", stage: "run-installer", status: result.status, error };
  emit(failed);
  return { ok: false, status: result.status, error, command };
}

module.exports = {
  bootstrapCacheDir,
  bootstrapManifest,
  buildBootstrapCommand,
  cachedScriptPath,
  candidateInstallScripts,
  installScriptKind,
  installScriptName,
  resolveLocalInstallScript,
  runBootstrap,
};
