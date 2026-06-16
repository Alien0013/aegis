const { execFileSync } = require("node:child_process");

function parseRegQueryValue(stdout, name) {
  if (!stdout || !name) return null;
  const typePattern =
    /^(\S+)\s+(?:REG_SZ|REG_EXPAND_SZ|REG_MULTI_SZ|REG_DWORD|REG_QWORD|REG_BINARY|REG_NONE)\s+(.*)$/;
  for (const rawLine of String(stdout).split(/\r?\n/)) {
    const line = rawLine.trim();
    const match = line.match(typePattern);
    if (match && match[1].toLowerCase() === String(name).toLowerCase()) {
      return match[2];
    }
  }
  return null;
}

function expandWindowsEnvRefs(value, env = process.env) {
  if (!value) return value;
  return String(value).replace(/%([^%]+)%/g, (whole, name) => {
    const key = Object.keys(env).find((candidate) => candidate.toUpperCase() === String(name).toUpperCase());
    return key != null && env[key] != null ? env[key] : whole;
  });
}

function readWindowsUserEnvVar(
  name,
  { platform = process.platform, env = process.env, exec = execFileSync } = {},
) {
  if (platform !== "win32" || !name) return null;
  let stdout;
  try {
    stdout = exec("reg", ["query", "HKCU\\Environment", "/v", name], {
      encoding: "utf8",
      windowsHide: true,
      timeout: 5000,
    });
  } catch {
    return null;
  }
  const raw = parseRegQueryValue(stdout, name);
  if (raw == null) return null;
  const expanded = expandWindowsEnvRefs(raw, env).trim();
  return expanded || null;
}

module.exports = {
  expandWindowsEnvRefs,
  parseRegQueryValue,
  readWindowsUserEnvVar,
};
