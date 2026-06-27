"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const wrapper = require("./desktop-uninstall.cjs");
const native = require("../../../desktop/electron/desktop-uninstall.cjs");

test("apps desktop uninstall wrapper delegates to native desktop module", () => {
  assert.equal(wrapper.desktopUninstallPlan, native.desktopUninstallPlan);
  assert.equal(wrapper.candidateDesktopUninstallScripts, native.candidateDesktopUninstallScripts);
  assert.equal(wrapper.desktopUninstallPlan({ platform: "win32" }).available, false);
});
