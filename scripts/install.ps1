# Compatibility wrapper for tooling that expects the installer under scripts/.
# Delegates to ../install.ps1.
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootInstaller = Join-Path (Split-Path -Parent $ScriptDir) "install.ps1"
& $RootInstaller @args
exit $LASTEXITCODE
