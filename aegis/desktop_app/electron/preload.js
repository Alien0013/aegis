// Preload for the boot splash — a tiny, locked-down bridge (contextIsolation on,
// nodeIntegration off). The splash can listen for boot status and ask the main
// process to retry / open logs / quit. Nothing else is exposed.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("aegisBoot", {
  onStatus: (cb) => ipcRenderer.on("boot:status", (_e, s) => cb(s)),
  retry: () => ipcRenderer.send("boot:retry"),
  openLogs: () => ipcRenderer.send("boot:openLogs"),
  quit: () => ipcRenderer.send("boot:quit"),
});
