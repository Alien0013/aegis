# AEGIS Desktop (Electron)

A native window around the AEGIS dashboard — double-click to run the whole
harness, no terminal needed. It spawns the local `aegis dashboard` server on a
random free port with a random token, then loads it.

## Run from source
```bash
cd desktop
npm install
npm start            # launches the app (requires `aegis` installed / on PATH)
```

## Build installers
```bash
npm run dist         # → dist/  (.dmg on macOS, .exe on Windows, .AppImage/.deb on Linux)
```
Signed/notarized installers need each platform's signing certs and (usually)
that platform's machine or CI runner. The app itself just needs `aegis`
available — set `AEGIS_BIN` to override the executable path.

## How it works
`main.js` → free port + random token → `spawn("aegis", ["dashboard","--port",P,"--no-open"])`
→ wait for `/` → `BrowserWindow.loadURL(http://127.0.0.1:P/?token=…)`. On quit it
stops the server. External links open in the system browser.
