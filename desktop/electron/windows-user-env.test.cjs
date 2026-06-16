const assert = require("node:assert/strict");
const test = require("node:test");
const {
  expandWindowsEnvRefs,
  parseRegQueryValue,
  readWindowsUserEnvVar,
} = require("./windows-user-env.cjs");

test("parses reg query output case-insensitively", () => {
  const output = [
    "HKEY_CURRENT_USER\\Environment",
    "    AEGIS_HOME    REG_EXPAND_SZ    %LOCALAPPDATA%\\aegis-data",
    "    OTHER         REG_SZ           keep me",
  ].join("\r\n");
  assert.equal(parseRegQueryValue(output, "aegis_home"), "%LOCALAPPDATA%\\aegis-data");
});

test("expands percent env refs while preserving unknown refs", () => {
  assert.equal(
    expandWindowsEnvRefs("%LOCALAPPDATA%\\aegis\\%MISSING%", { LOCALAPPDATA: "C:\\Users\\Alien\\AppData\\Local" }),
    "C:\\Users\\Alien\\AppData\\Local\\aegis\\%MISSING%",
  );
});

test("reads live HKCU env only on Windows", () => {
  const exec = () => "HKEY_CURRENT_USER\\Environment\n    AEGIS_BIN    REG_SZ    D:\\Tools\\aegis.exe\n";
  assert.equal(readWindowsUserEnvVar("AEGIS_BIN", { platform: "linux", exec }), null);
  assert.equal(readWindowsUserEnvVar("AEGIS_BIN", { platform: "win32", exec }), "D:\\Tools\\aegis.exe");
});
