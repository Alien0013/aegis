"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execSync } = require("node:child_process");

const STAMP_SCHEMA_VERSION = 2;

const TRUTHY = new Set(["1", "true", "yes", "on"]);
const FALSY = new Set(["0", "false", "no", "off"]);

function envFlag(env, name) {
  const value = String(env[name] || "").trim().toLowerCase();
  if (TRUTHY.has(value)) return true;
  if (FALSY.has(value)) return false;
  return false;
}

function cleanVersion(value) {
  return String(value || "").replace(/^[^\d]*/, "") || null;
}

function loadPackageJson(desktopRoot) {
  const packageFile = path.join(desktopRoot, "package.json");
  return JSON.parse(fs.readFileSync(packageFile, "utf8"));
}

function isReleaseBuild(env = process.env) {
  return (
    envFlag(env, "AEGIS_RELEASE") ||
    envFlag(env, "AEGIS_DESKTOP_RELEASE") ||
    envFlag(env, "CI_RELEASE")
  );
}

function inferTargetPlatforms({ env = process.env, platform = process.platform } = {}) {
  const explicit = env.AEGIS_DESKTOP_TARGETS || env.AEGIS_BUILD_TARGETS || env.AEGIS_BUILD_TARGET || "";
  if (explicit) {
    return Array.from(new Set(String(explicit).split(/[,\s]+/).map(normalizeTargetPlatform).filter(Boolean)));
  }
  const lifecycle = String(env.npm_lifecycle_event || "").toLowerCase();
  if (lifecycle.endsWith(":win")) return ["win32"];
  if (lifecycle.endsWith(":mac")) return ["darwin"];
  if (lifecycle.endsWith(":linux")) return ["linux"];
  return [platform];
}

function normalizeTargetPlatform(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) return "";
  if (["win", "windows", "win32"].includes(raw)) return "win32";
  if (["mac", "macos", "darwin", "osx"].includes(raw)) return "darwin";
  if (["linux", "appimage", "deb", "rpm"].includes(raw)) return "linux";
  return raw;
}

function hasPublishTarget(packageJson) {
  const publish = packageJson && packageJson.build && packageJson.build.publish;
  return Array.isArray(publish) ? publish.length > 0 : Boolean(publish);
}

function hasMacSigningMaterial(env) {
  return Boolean(
    env.CSC_LINK ||
    env.CSC_NAME ||
    env.APPLE_ID ||
    env.APPLE_API_KEY ||
    env.APPLE_API_KEY_ID
  );
}

function releaseBuildFailures({
  env = process.env,
  packageJson,
  stamp,
  targetPlatforms = inferTargetPlatforms({ env }),
} = {}) {
  if (!isReleaseBuild(env)) return [];
  const failures = [];
  const targets = new Set((targetPlatforms || []).map(normalizeTargetPlatform).filter(Boolean));

  if (stamp && stamp.source !== "ci" && !envFlag(env, "AEGIS_ALLOW_LOCAL_DESKTOP_RELEASE")) {
    failures.push("release builds must be stamped from CI or set AEGIS_ALLOW_LOCAL_DESKTOP_RELEASE=1");
  }
  if (stamp && stamp.dirty && !envFlag(env, "AEGIS_ALLOW_DIRTY_DESKTOP_RELEASE")) {
    failures.push("release builds cannot be created from a dirty worktree");
  }
  if (hasPublishTarget(packageJson) && !(env.GH_TOKEN || env.GITHUB_TOKEN) && !envFlag(env, "AEGIS_ALLOW_OFFLINE_DESKTOP_RELEASE")) {
    failures.push("release builds with GitHub publishing need GH_TOKEN/GITHUB_TOKEN or AEGIS_ALLOW_OFFLINE_DESKTOP_RELEASE=1");
  }

  const winConfig = packageJson && packageJson.build && packageJson.build.win ? packageJson.build.win : {};
  if (
    targets.has("win32") &&
    winConfig.signAndEditExecutable === false &&
    !envFlag(env, "AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE")
  ) {
    failures.push("Windows release builds cannot set build.win.signAndEditExecutable=false");
  }

  if (targets.has("darwin") && !hasMacSigningMaterial(env) && !envFlag(env, "AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE")) {
    failures.push("macOS release builds need signing/notarization credentials or AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE=1");
  }

  return failures;
}

function assertReleaseBuildReady(options = {}) {
  const failures = releaseBuildFailures(options);
  if (failures.length) {
    throw new Error(`unsafe desktop release build:\n- ${failures.join("\n- ")}`);
  }
}

function tryExec(cmd, cwd) {
  try {
    return execSync(cmd, {
      cwd,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return null;
  }
}

function findRepoRoot(start) {
  let dir = path.resolve(start);
  while (dir && dir !== path.dirname(dir)) {
    if (fs.existsSync(path.join(dir, ".git")) || fs.existsSync(path.join(dir, "pyproject.toml"))) {
      return dir;
    }
    dir = path.dirname(dir);
  }
  return path.resolve(start);
}

function stampFromEnv(env) {
  const commit = env.GITHUB_SHA || env.AEGIS_BUILD_COMMIT || "";
  if (!commit) return null;
  return {
    commit,
    branch: env.GITHUB_REF_NAME || env.GITHUB_HEAD_REF || env.AEGIS_BUILD_BRANCH || null,
    dirty: false,
    source: "ci",
  };
}

function stampFromGit(repoRoot) {
  const commit = tryExec("git rev-parse HEAD", repoRoot);
  if (!commit) return null;
  const branch = tryExec("git rev-parse --abbrev-ref HEAD", repoRoot);
  const status = tryExec("git status --porcelain -uno", repoRoot);
  return {
    commit,
    branch: branch && branch !== "HEAD" ? branch : null,
    dirty: status !== null && status.length > 0,
    source: "local",
  };
}

function resolveBuildStamp({ repoRoot, env = process.env } = {}) {
  const root = repoRoot || findRepoRoot(path.resolve(__dirname, ".."));
  return stampFromEnv(env) || stampFromGit(root);
}

function writeBuildStamp({
  desktopRoot = path.resolve(__dirname, ".."),
  repoRoot = findRepoRoot(desktopRoot),
  env = process.env,
  now = () => new Date(),
  packageJson = loadPackageJson(desktopRoot),
  platform = process.platform,
  arch = process.arch,
  versions = process.versions,
} = {}) {
  const stamp = resolveBuildStamp({ repoRoot, env });
  if (!stamp || !stamp.commit) {
    throw new Error(`could not determine build commit from env or git at ${repoRoot}`);
  }
  const targetPlatforms = inferTargetPlatforms({ env, platform });
  assertReleaseBuildReady({ env, packageJson, stamp, targetPlatforms });
  const release = isReleaseBuild(env);
  const payload = {
    schemaVersion: STAMP_SCHEMA_VERSION,
    appName: packageJson.productName || packageJson.name || "AEGIS",
    appVersion: packageJson.version || null,
    electronVersion: cleanVersion(
      packageJson.build && packageJson.build.electronVersion
        ? packageJson.build.electronVersion
        : packageJson.devDependencies && packageJson.devDependencies.electron,
    ),
    nodeVersion: versions.node || null,
    platform,
    arch,
    targetPlatforms,
    release,
    trustedRelease: release,
    commit: stamp.commit,
    branch: stamp.branch,
    builtAt: now().toISOString(),
    dirty: Boolean(stamp.dirty),
    source: stamp.source,
    ci: Boolean(env.CI || env.GITHUB_ACTIONS),
    workflow: env.GITHUB_WORKFLOW || null,
    runId: env.GITHUB_RUN_ID || null,
  };
  const outDir = path.join(desktopRoot, "build");
  const outFile = path.join(outDir, "install-stamp.json");
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(outFile, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  return { payload, path: outFile };
}

function main() {
  const result = writeBuildStamp();
  const short = result.payload.commit.slice(0, 12);
  const branch = result.payload.branch ? ` (${result.payload.branch})` : "";
  const dirty = result.payload.dirty ? " [DIRTY]" : "";
  console.log(`[write-build-stamp] wrote ${path.relative(process.cwd(), result.path)} -> ${short}${branch}${dirty}`);
}

module.exports = {
  STAMP_SCHEMA_VERSION,
  assertReleaseBuildReady,
  findRepoRoot,
  inferTargetPlatforms,
  isReleaseBuild,
  releaseBuildFailures,
  resolveBuildStamp,
  writeBuildStamp,
};

if (require.main === module) {
  try {
    main();
  } catch (err) {
    console.error(`[write-build-stamp] ERROR: ${err.message}`);
    process.exit(1);
  }
}
