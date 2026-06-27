"use strict";

const fs = require("node:fs");
const path = require("node:path");

function copyDesktopUninstallScript({ desktopRoot = path.resolve(__dirname, "..") } = {}) {
  const source = path.resolve(desktopRoot, "..", "uninstall.sh");
  const target = path.join(desktopRoot, "build", "uninstall.sh");
  if (!fs.existsSync(source)) {
    throw new Error(`native uninstall script not found: ${source}`);
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.copyFileSync(source, target);
  fs.chmodSync(target, 0o755);
  return target;
}

if (require.main === module) {
  const target = copyDesktopUninstallScript();
  console.log(`staged uninstall script: ${target}`);
}

module.exports = { copyDesktopUninstallScript };
