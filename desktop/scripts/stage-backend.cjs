"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { inferTargetPlatforms } = require("./write-build-stamp.cjs");

const BACKEND_MANIFEST_SCHEMA_VERSION = 1;
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

function _markExecutable(target) {
  try {
    if (fs.statSync(target).isFile()) fs.chmodSync(target, 0o755);
  } catch {
    // Some packaging filesystems, especially on Windows, do not expose POSIX modes.
  }
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
    createdAt: now().toISOString(),
  };

  if (!sourcePath) {
    manifest.reason = "AEGIS_DESKTOP_BACKEND_SOURCE is not set; packaged app will use configured installs or PATH";
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
    fs.cpSync(sourcePath, paths.backendDir, { recursive: true });
    for (const command of commands) _markExecutable(path.join(paths.backendDir, command));
    manifest.staged = true;
    manifest.mode = "directory";
    manifest.targets = commands;
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
