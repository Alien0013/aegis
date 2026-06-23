# AEGIS one-line installer for Windows (PowerShell).
#   irm https://raw.githubusercontent.com/Alien0013/aegis/main/install.ps1 | iex
#   # or from a clone:  ./install.ps1
$ErrorActionPreference = "Stop"

$InstallDir = if ($env:AEGIS_INSTALL_DIR) { $env:AEGIS_INSTALL_DIR } else { "$HOME\.aegis\venv" }
$Extras     = if ($null -ne $env:AEGIS_EXTRAS) { $env:AEGIS_EXTRAS } else { "all" }
$Repo       = $env:AEGIS_REPO
$Branch     = if ($env:AEGIS_BRANCH) { $env:AEGIS_BRANCH } else { "main" }
$RunOnboard = if ($env:AEGIS_ONBOARD -eq "0") { $false } else { $true }
$NoPrompt   = $env:AEGIS_NO_PROMPT -eq "1"
$Verify     = $env:AEGIS_VERIFY_INSTALL -eq "1"
$OnboardArgs = if ($env:AEGIS_ONBOARD_ARGS) { $env:AEGIS_ONBOARD_ARGS } else { "" }
$InstallToolsets = if ($env:AEGIS_TOOLSETS) { $env:AEGIS_TOOLSETS } else { "" }
$InstallSkills = if ($env:AEGIS_SKILLS) { $env:AEGIS_SKILLS } else { "" }

function Say($m){ Write-Host "▸ $m" -ForegroundColor Magenta }
function Ok($m){ Write-Host "✓ $m" -ForegroundColor Green }
function Warn($m){ Write-Host "! $m" -ForegroundColor Yellow }
function HasTty(){ return (-not $NoPrompt) -and (-not [Console]::IsInputRedirected) -and (-not [Console]::IsOutputRedirected) }

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
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
if ($Repo) { $source = "git+$Repo" }
elseif ($scriptDir -and (Test-Path "$scriptDir\pyproject.toml")) { $source = $scriptDir }
else {
  $pkg = "aegis-agent-harness"
  if ($Extras) { $pkg = "$pkg[$Extras]" }
  $source = "$pkg @ git+https://github.com/Alien0013/aegis.git@$Branch"
}
if ($Extras -and $source -notlike "* @ git+*" -and $source -notlike "*[$Extras]") { $source = "$source[$Extras]" }
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

$aegis = Join-Path $binDir "aegis.exe"
if (-not (Test-Path $aegis)) { $aegis = Join-Path $binDir "aegis" }

if ($RunOnboard -and (HasTty)) {
  Say "Starting first-run onboarding..."
  $args = @("setup")
  if ($OnboardArgs.Trim()) { $args += ($OnboardArgs.Trim() -split '\s+') }
  if ($InstallToolsets.Trim()) { $args += @("--toolsets", $InstallToolsets.Trim()) }
  if ($InstallSkills.Trim()) { $args += @("--skills", $InstallSkills.Trim()) }
  & $aegis @args
  if ($LASTEXITCODE -ne 0) { Warn "Onboarding did not finish. Run 'aegis setup' when ready." }
}
elseif ($RunOnboard) {
  Warn "Onboarding skipped because this shell is not interactive. Run 'aegis setup' when ready."
}
else {
  Warn "Onboarding skipped. Run 'aegis setup' when ready."
}

if ($Verify) {
  Say "Verifying installation..."
  & $aegis doctor
}

Ok "AEGIS installed."
Write-Host ""
Write-Host "Commands" -ForegroundColor Magenta
Write-Host "  Start:   aegis"
Write-Host "  Setup:   aegis setup"
Write-Host "  Status:  aegis status"
Write-Host "  Doctor:  aegis doctor"
