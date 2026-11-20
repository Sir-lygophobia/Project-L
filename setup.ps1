[CmdletBinding()]
param(
    [switch]$NoGui,
    [switch]$SkipConfiguration,
    [switch]$SkipSystemDependencies
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

function Update-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathParts = @($machinePath, $userPath, $env:Path) | Where-Object { $_ }
    $env:Path = $pathParts -join ";"
}

function Install-SystemPackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DisplayName,
        [Parameter(Mandatory = $true)]
        [string]$WingetId,
        [Parameter(Mandatory = $true)]
        [string]$ChocolateyId,
        [switch]$Required
    )

    if ($SkipSystemDependencies) {
        if ($Required) {
            throw "$DisplayName is required but automatic system dependency installation was skipped."
        }
        Write-Warning "$DisplayName is missing and automatic installation was skipped."
        return $false
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing $DisplayName with Windows Package Manager..."
        & winget install `
            --id $WingetId `
            --exact `
            --silent `
            --accept-package-agreements `
            --accept-source-agreements `
            --disable-interactivity
        if ($LASTEXITCODE -eq 0) {
            Update-ProcessPath
            return $true
        }
        Write-Warning "Windows Package Manager could not install $DisplayName (exit $LASTEXITCODE)."
    }

    if (Get-Command choco -ErrorAction SilentlyContinue) {
        Write-Host "Installing $DisplayName with Chocolatey..."
        & choco install $ChocolateyId --yes --no-progress
        if ($LASTEXITCODE -eq 0) {
            Update-ProcessPath
            return $true
        }
        Write-Warning "Chocolatey could not install $DisplayName (exit $LASTEXITCODE)."
    }

    if ($Required) {
        throw "Could not install $DisplayName. Install it manually, reopen PowerShell, and run setup.ps1 again."
    }
    Write-Warning "Could not install $DisplayName automatically. The related optional feature may be unavailable."
    return $false
}

function Find-Python311 {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3.11 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{
                Command = $launcher.Source
                Arguments = @("-3.11")
            }
        }
    }

    $candidates = @()
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $candidates += $pythonCommand.Source
    }
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    }
    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles "Python311\python.exe"
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            continue
        }
        & $candidate -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{
                Command = $candidate
                Arguments = @()
            }
        }
    }
    return $null
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
    $python311 = Find-Python311
    if (-not $python311) {
        Install-SystemPackage `
            -DisplayName "Python 3.11" `
            -WingetId "Python.Python.3.11" `
            -ChocolateyId "python311" | Out-Null
        Update-ProcessPath
        $python311 = Find-Python311
    }
    if (-not $python311) {
        throw "Python 3.11 is still unavailable. Install it, reopen PowerShell, and run setup.ps1 again."
    }
    Write-Host "Creating a Python 3.11 virtual environment..."
    & $python311.Command @($python311.Arguments) -m venv $venvDirectory
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
    Install-SystemPackage `
        -DisplayName "FFmpeg" `
        -WingetId "Gyan.FFmpeg" `
        -ChocolateyId "ffmpeg" | Out-Null
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Host "FFmpeg installed."
    }
    else {
        Write-Warning "FFmpeg is not available in this PowerShell session. Reopen PowerShell before using /play."
    }
}

if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "Node.js found."
}
else {
    Install-SystemPackage `
        -DisplayName "Node.js LTS" `
        -WingetId "OpenJS.NodeJS.LTS" `
        -ChocolateyId "nodejs-lts" | Out-Null
    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Host "Node.js LTS installed."
    }
    else {
        Write-Warning "Node.js is not available in this PowerShell session. Reopen PowerShell if yt-dlp requests a JavaScript runtime."
    }
}

Write-Host "Creating a recoverable database snapshot before startup checks..."
& $venvPython (Join-Path $PSScriptRoot "runtime_backup.py") backup --keep 14 --quiet
$backupCode = $LASTEXITCODE
Assert-NativeSuccess -Operation "Runtime database backup" -ExitCode $backupCode

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
