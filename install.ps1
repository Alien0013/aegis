# AEGIS one-line installer for Windows (PowerShell).
#   irm https://raw.githubusercontent.com/Alien0013/aegis/main/install.ps1 | iex
#   # or from a clone:  ./install.ps1
$ErrorActionPreference = "Stop"

$InstallDir = if ($env:AEGIS_INSTALL_DIR) { $env:AEGIS_INSTALL_DIR } else { "$HOME\.aegis\venv" }
$Extras     = $env:AEGIS_EXTRAS
$Repo       = $env:AEGIS_REPO

function Say($m){ Write-Host "▸ $m" -ForegroundColor Magenta }
function Ok($m){ Write-Host "✓ $m" -ForegroundColor Green }

# 1. find python >= 3.10
$py = $null
foreach ($c in @("python","python3","py")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) {
    $ok = & $c -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $c; break }
  }
}
if (-not $py) { throw "Python 3.10+ not found. Install from python.org and re-run." }
Ok "Using $(& $py --version)"

# 2. source
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($Repo) { $source = "git+$Repo" }
elseif (Test-Path "$scriptDir\pyproject.toml") { $source = $scriptDir }
else { $source = "aegis-agent" }
if ($Extras) { $source = "$source[$Extras]" }
Say "Install source: $source"

# 3. venv + install
Say "Creating venv at $InstallDir…"
& $py -m venv $InstallDir
& "$InstallDir\Scripts\pip.exe" install -q --upgrade pip wheel
& "$InstallDir\Scripts\pip.exe" install -q $source
Ok "Installed."

# 4. PATH (user scope)
$binDir = "$InstallDir\Scripts"
$userPath = [Environment]::GetEnvironmentVariable("Path","User")
if ($userPath -notlike "*$binDir*") {
  [Environment]::SetEnvironmentVariable("Path","$binDir;$userPath","User")
  Ok "Added $binDir to your PATH (restart the terminal)."
}
Ok "AEGIS installed. Run 'aegis setup' then 'aegis'."
