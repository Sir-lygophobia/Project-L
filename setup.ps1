[CmdletBinding()]
param(
    [switch]$NoGui,
    [switch]$SkipConfiguration
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = "1"

function Assert-NativeSuccess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Operation,
        [Parameter(Mandatory = $true)]
        [int]$ExitCode
    )
    if ($ExitCode -ne 0) {
        throw "$Operation failed with exit code $ExitCode."
    }
}

$configPath = Join-Path $PSScriptRoot "Data\config.json"
$configExamplePath = Join-Path $PSScriptRoot "Data\config.example.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    Copy-Item -LiteralPath $configExamplePath -Destination $configPath
    Write-Host "Created Data\config.json from the safe template."
}

$rolesPath = Join-Path $PSScriptRoot "Data\roles.json"
$rolesExamplePath = Join-Path $PSScriptRoot "Data\roles.example.json"
if (-not (Test-Path -LiteralPath $rolesPath)) {
    Copy-Item -LiteralPath $rolesExamplePath -Destination $rolesPath
    Write-Host "Created Data\roles.json from the safe template."
}

$venvDirectory = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"
$requirementsPath = Join-Path $PSScriptRoot "requirements.txt"
$requirementsHashPath = Join-Path $PSScriptRoot ".requirements.sha256"

if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
    throw "requirements.txt is missing. Restore it before running setup."
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Python launcher not found. Install Python 3.11 from python.org."
    }
    Write-Host "Creating a Python 3.11 virtual environment..."
    & py -3.11 -m venv $venvDirectory
    $venvCode = $LASTEXITCODE
    Assert-NativeSuccess -Operation "Virtual environment creation" -ExitCode $venvCode
}

$pythonVersion = & $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$versionCode = $LASTEXITCODE
Assert-NativeSuccess -Operation "Python version check" -ExitCode $versionCode
if ($pythonVersion -ne "3.11") {
    throw "This setup is tested with Python 3.11; found $pythonVersion. Remove .venv and install Python 3.11."
}

Write-Host "Installing Project L dependencies with Python $pythonVersion..."
& $venvPython -m pip install --upgrade pip
$pipUpgradeCode = $LASTEXITCODE
Assert-NativeSuccess -Operation "pip upgrade" -ExitCode $pipUpgradeCode

& $venvPython -m pip install --requirement $requirementsPath
$pipInstallCode = $LASTEXITCODE
Assert-NativeSuccess -Operation "Dependency installation" -ExitCode $pipInstallCode

& $venvPython -m pip check
$pipCheckCode = $LASTEXITCODE
Assert-NativeSuccess -Operation "Dependency consistency check" -ExitCode $pipCheckCode

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "FFmpeg found."
}
else {
    Write-Warning "FFmpeg was not found on PATH. Install it before using /play."
}

if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "Node.js found."
}
else {
    Write-Warning "Node.js was not found. Current yt-dlp releases may need a JavaScript runtime for YouTube."
}

Write-Host "Checking the installation..."
& $venvPython (Join-Path $PSScriptRoot "bot.py") --check
$botCheckCode = $LASTEXITCODE
Assert-NativeSuccess -Operation "Project L installation check" -ExitCode $botCheckCode

# Only mark this dependency set as installed after pip and the offline bot check pass.
$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $requirementsHashPath -Value $requirementsHash -Encoding ascii -NoNewline

if ($SkipConfiguration) {
    Write-Host "Dependency setup and installation checks completed."
    return
}

$configurePath = Join-Path $PSScriptRoot "configure_gui.py"
& $venvPython $configurePath --check --quiet
$configCode = $LASTEXITCODE
if ($configCode -eq 2) {
    if ($NoGui) {
        throw "Configuration is incomplete. Run .\setup.ps1 without -NoGui or run .\configure.ps1."
    }
    Write-Host "Opening the Project L configuration wizard..."
    & $venvPython $configurePath
    $guiCode = $LASTEXITCODE
    if ($guiCode -ne 0) {
        throw "Configuration was cancelled or failed with exit code $guiCode."
    }
    & $venvPython $configurePath --check --quiet
    $configCode = $LASTEXITCODE
}
if ($configCode -ne 0) {
    throw "Configuration validation failed with exit code $configCode."
}

Write-Host "Setup complete. Run .\start.ps1 to launch the bot."
