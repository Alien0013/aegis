
if ($env:AEGIS_LIVE_DESKTOP_WINDOWS -ne "1") {
  Write-Output "skipped: set AEGIS_LIVE_DESKTOP_WINDOWS=1 on a Windows desktop runner"
  exit 0
}
if (-not (Test-Path "desktop/package.json")) { throw "desktop/package.json not found" }
if (-not (Test-Path "desktop/electron/main.js")) { throw "desktop/electron/main.js not found" }
Write-Output "windows desktop runner preflight ok"
