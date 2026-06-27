"use strict";

const fs = require("node:fs");
const path = require("node:path");

function _unique(paths) {
  return Array.from(new Set(paths.filter(Boolean).map((item) => path.resolve(item))));
}

function candidateDesktopUninstallScripts({
  desktopRoot = path.resolve(__dirname, ".."),
  resourcesPath = process.resourcesPath || "",
} = {}) {
  const candidates = [];
  if (resourcesPath) candidates.push(path.join(resourcesPath, "uninstall.sh"));
  if (desktopRoot) candidates.push(path.resolve(desktopRoot, "..", "uninstall.sh"));
  return _unique(candidates);
}

function desktopUninstallPlan({
  desktopRoot = path.resolve(__dirname, ".."),
  resourcesPath = process.resourcesPath || "",
  platform = process.platform,
  exists = fs.existsSync,
  purge = false,
} = {}) {
  if (platform === "win32") {
    return {
      available: false,
      reason: "Use the Windows app uninstaller from Settings or Control Panel.",
      command: "",
      args: [],
      scriptPath: "",
    };
  }
  for (const scriptPath of candidateDesktopUninstallScripts({ desktopRoot, resourcesPath })) {
    try {
      if (!exists(scriptPath)) continue;
    } catch {
      continue;
    }
    const args = [scriptPath];
    if (purge) args.push("--purge");
    return { available: true, reason: "", command: "bash", args, scriptPath };
  }
  return {
    available: false,
    reason: "AEGIS uninstall script was not found in packaged resources or the source checkout.",
    command: "",
    args: [],
    scriptPath: "",
  };
}

module.exports = {
  candidateDesktopUninstallScripts,
  desktopUninstallPlan,
};
