// Preload for the main app window — a locked-down bridge (contextIsolation on,
// nodeIntegration off) that the React UI uses to drive the frameless native
// shell: window controls (the OS frame is hidden on Linux/Windows so we draw our
// own titlebar), maximize-state changes, open-external, and backend restart.
// Exposed as window.aegisDesktop; its presence is also how the UI knows it is
// running inside the desktop app rather than a browser tab.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("aegisDesktop", {
  isDesktop: true,
  platform: process.platform,
  minimize: () => ipcRenderer.send("win:minimize"),
  maximizeToggle: () => ipcRenderer.send("win:maximizeToggle"),
  close: () => ipcRenderer.send("win:close"),
  isMaximized: () => ipcRenderer.invoke("win:isMaximized"),
  onMaximizeChange: (cb) => {
    const h = (_e, v) => cb(!!v);
    ipcRenderer.on("win:maximized", h);
    return () => ipcRenderer.removeListener("win:maximized", h);
  },
  openExternal: (url) => ipcRenderer.send("win:openExternal", url),
  restartBackend: () => ipcRenderer.send("win:restartBackend"),
  getConnection: () => ipcRenderer.invoke("aegis:connection"),
  getDiagnostics: () => ipcRenderer.invoke("aegis:diagnostics"),
  runRepairAction: (action) => ipcRenderer.invoke("aegis:repair", action),
  api: (request) => ipcRenderer.invoke("aegis:api", request),
  getRecentLogs: (limit) => ipcRenderer.invoke("aegis:logs:recent", { limit }),
  revealLogs: () => ipcRenderer.invoke("aegis:logs:reveal"),
  checkForUpdates: () => ipcRenderer.invoke("aegis:update:check"),
  getUpdateStatus: () => ipcRenderer.invoke("aegis:update:status"),
  installUpdate: () => ipcRenderer.invoke("aegis:update:install"),
  getSettings: () => ipcRenderer.invoke("aegis:settings:get"),
  setDefaultProjectDir: (path) => ipcRenderer.invoke("aegis:settings:setDefaultProjectDir", path),
  setRemoteBackend: (settings) => ipcRenderer.invoke("aegis:settings:setRemoteBackend", settings),
  chooseProjectDir: () => ipcRenderer.invoke("aegis:settings:chooseProjectDir"),
});
