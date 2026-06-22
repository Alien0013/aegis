"use strict";

const fs = require("node:fs");
const crypto = require("node:crypto");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const {
  inferTargetPlatforms,
  isReleaseBuild,
} = require("./write-build-stamp.cjs");

const BACKEND_MANIFEST_SCHEMA_VERSION = 1;
const DEFAULT_PROBE_TIMEOUT_MS = 2500;
const WIN_EXTENSIONS = new Set([".exe", ".cmd", ".bat"]);
const RELATIVE_COMMANDS = [
  path.join("bin", "aegis"),
  path.join("Scripts", "aegis.exe"),
  path.join("Scripts", "aegis.cmd"),
  path.join("Scripts", "aegis.bat"),
  "aegis",
];

function backendStagePaths({ desktopRoot = path.resolve(__dirname, "..") } = {}) {
  const buildDir = path.join(desktopRoot, "build");
  return {
    buildDir,
    backendDir: path.join(buildDir, "backend"),
    manifestPath: path.join(buildDir, "backend-manifest.json"),
  };
}

function _writeJson(file, payload) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

function _relativeFromBackend(backendDir, target) {
  return path.relative(backendDir, target).replace(/\\/g, "/");
}

function _knownCommandsIn(root) {
  return RELATIVE_COMMANDS
    .map((rel) => path.join(root, rel))
    .filter((candidate) => {
      try { return fs.existsSync(candidate) && fs.statSync(candidate).isFile(); } catch { return false; }
    })
    .map((candidate) => path.relative(root, candidate).replace(/\\/g, "/"));
}

function _isInside(root, target) {
  const relative = path.relative(root, target);
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function _validateDirectorySymlinks(root) {
  const realRoot = fs.realpathSync(root);
  const visit = (dir) => {
    for (const name of fs.readdirSync(dir).sort()) {
      const candidate = path.join(dir, name);
      const rel = path.relative(root, candidate).replace(/\\/g, "/");
      const stat = fs.lstatSync(candidate);
      if (stat.isSymbolicLink()) {
        let realTarget;
        try {
          realTarget = fs.realpathSync(candidate);
        } catch {
          throw new Error(`desktop backend source contains a broken symlink: ${rel}`);
        }
        if (!_isInside(realRoot, realTarget)) {
          throw new Error(`desktop backend source contains an unsafe symlink outside the source tree: ${rel}`);
        }
        continue;
      }
      if (stat.isDirectory()) visit(candidate);
    }
  };
  visit(root);
}

function _materializeSymlinks(root) {
  const visit = (dir) => {
    for (const name of fs.readdirSync(dir).sort()) {
      const candidate = path.join(dir, name);
      const stat = fs.lstatSync(candidate);
      if (stat.isSymbolicLink()) {
        const realTarget = fs.realpathSync(candidate);
        const targetStat = fs.statSync(candidate);
        fs.unlinkSync(candidate);
        if (targetStat.isDirectory()) {
          fs.cpSync(realTarget, candidate, { recursive: true, dereference: true });
          visit(candidate);
        } else if (targetStat.isFile()) {
          fs.copyFileSync(realTarget, candidate);
        }
        continue;
      }
      if (stat.isDirectory()) visit(candidate);
    }
  };
  visit(root);
}

function _hostCanRunTarget(rel, platform) {
  const clean = String(rel || "").replace(/\\/g, "/");
  if (platform === "win32") {
    return clean.startsWith("Scripts/") && WIN_EXTENSIONS.has(path.extname(clean).toLowerCase());
  }
  return clean === "aegis" || clean === "bin/aegis";
}

function _probeStagedCommand(command, rel, { env, platform, probeCommand, probeTimeoutMs } = {}) {
  if (!_hostCanRunTarget(rel, platform)) return { rel, skipped: true, reason: `not runnable on ${platform}` };
  if (typeof probeCommand === "function") {
    if (!probeCommand(command, rel)) throw new Error(`staged backend command failed version probe: ${rel}`);
    return { rel, skipped: false };
  }
  try {
    execFileSync(command, ["--version"], {
      env,
      stdio: "ignore",
      timeout: probeTimeoutMs || DEFAULT_PROBE_TIMEOUT_MS,
      windowsHide: true,
    });
    return { rel, skipped: false };
  } catch (err) {
    throw new Error(`staged backend command failed version probe: ${rel}: ${err.message}`);
  }
}

function _probeStagedCommands(backendDir, relCommands, options = {}) {
  return relCommands.map((rel) => _probeStagedCommand(path.join(backendDir, rel), rel, options));
}

function _markExecutable(target) {
  try {
    if (fs.statSync(target).isFile()) fs.chmodSync(target, 0o755);
  } catch {
    // Some packaging filesystems, especially on Windows, do not expose POSIX modes.
  }
}

function _walkFiles(root) {
  const rows = [];
  const visit = (dir) => {
    for (const name of fs.readdirSync(dir).sort()) {
      const candidate = path.join(dir, name);
      let stat;
      try {
        stat = fs.statSync(candidate);
      } catch {
        continue;
      }
      if (stat.isDirectory()) {
        visit(candidate);
      } else if (stat.isFile()) {
        rows.push(candidate);
      }
    }
  };
  if (fs.existsSync(root)) visit(root);
  return rows;
}

function _sha256File(file) {
  const hash = crypto.createHash("sha256");
  hash.update(fs.readFileSync(file));
  return hash.digest("hex");
}

function _summarizeStagedBackend(manifest, backendDir) {
  const files = _walkFiles(backendDir).map((file) => {
    const stat = fs.statSync(file);
    return {
      path: _relativeFromBackend(backendDir, file),
      size: stat.size,
      sha256: _sha256File(file),
    };
  });
  const digest = crypto.createHash("sha256");
  for (const file of files) {
    digest.update(file.path);
    digest.update("\0");
    digest.update(String(file.size));
    digest.update("\0");
    digest.update(file.sha256);
    digest.update("\0");
  }
  manifest.files = files;
  manifest.fileCount = files.length;
  manifest.totalBytes = files.reduce((total, file) => total + file.size, 0);
  manifest.sha256 = files.length ? digest.digest("hex") : "";
}

function _copyExecutable(source, target) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.copyFileSync(source, target);
  _markExecutable(target);
}

function _cleanBackendDir(backendDir) {
  fs.rmSync(backendDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  fs.mkdirSync(backendDir, { recursive: true });
}

function _sourceFromEnv(env) {
  return env.AEGIS_DESKTOP_BACKEND_SOURCE || env.AEGIS_DESKTOP_BACKEND || "";
}

function _envFlag(env, name) {
  return ["1", "true", "yes", "on"].includes(String(env[name] || "").trim().toLowerCase());
}

function _sourceStat(source) {
  if (!source) return null;
  try {
    return fs.statSync(source);
  } catch {
    throw new Error(`desktop backend source does not exist: ${source}`);
  }
}

function stageBackend({
  desktopRoot = path.resolve(__dirname, ".."),
  env = process.env,
  platform = process.platform,
  now = () => new Date(),
  probeCommand = null,
  probeTimeoutMs = DEFAULT_PROBE_TIMEOUT_MS,
} = {}) {
  const source = String(_sourceFromEnv(env) || "").trim();
  const sourcePath = source ? path.resolve(source) : "";
  const targetPlatforms = inferTargetPlatforms({ env, platform });
  const paths = backendStagePaths({ desktopRoot });
  _cleanBackendDir(paths.backendDir);

  const manifest = {
    schemaVersion: BACKEND_MANIFEST_SCHEMA_VERSION,
    staged: false,
    mode: "none",
    source: sourcePath || null,
    targetPlatforms,
    targets: [],
    warnings: [],
    reason: "",
    files: [],
    fileCount: 0,
    totalBytes: 0,
    sha256: "",
    createdAt: now().toISOString(),
  };

  if (!sourcePath) {
    if (isReleaseBuild(env) && !_envFlag(env, "AEGIS_ALLOW_EXTERNAL_DESKTOP_BACKEND")) {
      throw new Error(
        "release desktop builds need AEGIS_DESKTOP_BACKEND_SOURCE or "
        + "AEGIS_ALLOW_EXTERNAL_DESKTOP_BACKEND=1",
      );
    }
    manifest.reason = "AEGIS_DESKTOP_BACKEND_SOURCE is not set; packaged app will use configured installs or PATH";
    if (isReleaseBuild(env)) {
      manifest.mode = "external";
      manifest.externalBackend = true;
      manifest.reason = "AEGIS_ALLOW_EXTERNAL_DESKTOP_BACKEND=1; packaged app will use configured installs or PATH";
    }
    fs.writeFileSync(path.join(paths.backendDir, ".placeholder"), "AEGIS desktop backend not staged.\n", "utf8");
    _writeJson(paths.manifestPath, manifest);
    return { manifest, ...paths };
  }

  const stat = _sourceStat(sourcePath);
  if (stat.isDirectory()) {
    const commands = _knownCommandsIn(sourcePath);
    if (!commands.length) {
      throw new Error(
        `desktop backend directory must contain one of: ${RELATIVE_COMMANDS.join(", ")}`,
      );
    }
    _validateDirectorySymlinks(sourcePath);
    fs.cpSync(sourcePath, paths.backendDir, { recursive: true, dereference: true });
    _materializeSymlinks(paths.backendDir);
    for (const command of commands) _markExecutable(path.join(paths.backendDir, command));
    manifest.commandProbes = _probeStagedCommands(paths.backendDir, commands, {
      env,
      platform,
      probeCommand,
      probeTimeoutMs,
    });
    manifest.staged = true;
    manifest.mode = "directory";
    manifest.targets = commands;
    _summarizeStagedBackend(manifest, paths.backendDir);
    _writeJson(paths.manifestPath, manifest);
    return { manifest, ...paths };
  }

  if (!stat.isFile()) {
    throw new Error(`desktop backend source must be a file or directory: ${sourcePath}`);
  }

  const wantsPosix = targetPlatforms.some((item) => item === "linux" || item === "darwin");
  const wantsWindows = targetPlatforms.includes("win32");
  const ext = path.extname(sourcePath).toLowerCase();
  const targets = [];

  if (wantsPosix) {
    targets.push(path.join(paths.backendDir, "bin", "aegis"));
  }

  if (wantsWindows) {
    if (!WIN_EXTENSIONS.has(ext)) {
      if (!wantsPosix) {
        throw new Error("Windows desktop backend builds need an .exe, .cmd, or .bat backend source");
      }
      manifest.warnings.push("backend source is not a Windows executable; no Scripts/aegis.* file was staged");
    } else {
      targets.push(path.join(paths.backendDir, "Scripts", `aegis${ext}`));
    }
  }

  if (!targets.length) {
    targets.push(path.join(paths.backendDir, "bin", "aegis"));
  }

  for (const target of targets) _copyExecutable(sourcePath, target);
  manifest.staged = true;
  manifest.mode = "file";
  manifest.targets = targets.map((target) => _relativeFromBackend(paths.backendDir, target));
  manifest.commandProbes = _probeStagedCommands(paths.backendDir, manifest.targets, {
    env,
    platform,
    probeCommand,
    probeTimeoutMs,
  });
  _summarizeStagedBackend(manifest, paths.backendDir);
  _writeJson(paths.manifestPath, manifest);
  return { manifest, ...paths };
}

function main() {
  const result = stageBackend();
  const relManifest = path.relative(process.cwd(), result.manifestPath);
  if (result.manifest.staged) {
    console.log(`[stage-backend] staged backend -> ${result.manifest.targets.join(", ")} (${relManifest})`);
  } else {
    console.log(`[stage-backend] no backend staged (${result.manifest.reason}); wrote ${relManifest}`);
  }
}

module.exports = {
  BACKEND_MANIFEST_SCHEMA_VERSION,
  backendStagePaths,
  stageBackend,
};

if (require.main === module) {
  try {
    main();
  } catch (err) {
    console.error(`[stage-backend] ERROR: ${err.message}`);
    process.exit(1);
  }
}
