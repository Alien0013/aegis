// Source-run launcher. Chromium checks the Linux setuid sandbox before app
// main.js runs, so --no-sandbox must be on Electron's argv, not appended later.
const { spawn } = require("child_process");
const electron = require("electron");

const useSandbox = process.argv.includes("--sandbox") || process.env.AEGIS_ELECTRON_SANDBOX === "1";
const args = [];
if (process.platform === "linux" && !useSandbox) {
  args.push("--no-sandbox");
}
args.push(".");

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const child = spawn(electron, args, {
  stdio: "inherit",
  env,
});

child.on("error", (err) => {
  console.error(`Could not launch Electron: ${err.message}`);
  process.exit(1);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (!child.killed) {
      child.kill(signal);
    }
  });
}

child.on("close", (code, signal) => {
  if (signal) {
    console.error(`Electron exited with signal ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 0);
});
