"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execSync } = require("node:child_process");

const STAMP_SCHEMA_VERSION = 1;

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
} = {}) {
  const stamp = resolveBuildStamp({ repoRoot, env });
  if (!stamp || !stamp.commit) {
    throw new Error(`could not determine build commit from env or git at ${repoRoot}`);
  }
  const payload = {
    schemaVersion: STAMP_SCHEMA_VERSION,
    commit: stamp.commit,
    branch: stamp.branch,
    builtAt: now().toISOString(),
    dirty: Boolean(stamp.dirty),
    source: stamp.source,
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
  findRepoRoot,
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
