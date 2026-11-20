[CmdletBinding()]
param(
    [switch]$NoGui,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BotArguments
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$configPath = Join-Path $PSScriptRoot "Data\config.json"
$rolesPath = Join-Path $PSScriptRoot "Data\roles.json"
$configurePath = Join-Path $PSScriptRoot "configure_gui.py"
$requirementsPath = Join-Path $PSScriptRoot "requirements.txt"
$requirementsHashPath = Join-Path $PSScriptRoot ".requirements.sha256"
$installationCheckOnly = @($BotArguments) -contains "--check"

function Test-DependencyState {
    if (
        -not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or
        -not (Test-Path -LiteralPath $requirementsPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $requirementsHashPath -PathType Leaf)
    ) {
        return $false
    }

    try {
        $expectedHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
        $installedHash = (Get-Content -LiteralPath $requirementsHashPath -Raw).Trim()
        return $installedHash -and ($installedHash -ieq $expectedHash)
    }
    catch {
        return $false
    }
}

if (
    -not (Test-DependencyState) -or
    -not (Test-Path -LiteralPath $configPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $rolesPath -PathType Leaf)
) {
    & (Join-Path $PSScriptRoot "setup.ps1") `
        -NoGui:$NoGui `
        -SkipConfiguration:$installationCheckOnly
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Setup did not create the expected virtual environment."
}
if (-not (Test-DependencyState)) {
    throw "Setup did not install the current requirements.txt dependency set."
}

if (-not $installationCheckOnly) {
    & $venvPython $configurePath --check --quiet
    $configCode = $LASTEXITCODE
    if ($configCode -eq 2) {
        if ($NoGui) {
            Write-Warning "Configuration is incomplete. Run .\configure.ps1 or start without -NoGui."
            exit 2
        }
        Write-Host "Opening the Project L configuration wizard..."
        & $venvPython $configurePath
        $guiCode = $LASTEXITCODE
        if ($guiCode -ne 0) {
            Write-Warning "Configuration was cancelled or could not be saved."
            exit 2
        }
        & $venvPython $configurePath --check --quiet
        $configCode = $LASTEXITCODE
    }
    if ($configCode -ne 0) {
        Write-Warning "Configuration validation failed with exit code $configCode."
        exit $configCode
    }
}

if ($installationCheckOnly) {
    & $venvPython (Join-Path $PSScriptRoot "bot.py") @BotArguments
    exit $LASTEXITCODE
}

# A named mutex has no stale lock file and is automatically abandoned if the
# launching PowerShell process crashes. Hashing the resolved workspace keeps
# separate Project L checkouts independent.
$workspacePath = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd("\").ToUpperInvariant()
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $workspaceBytes = [System.Text.Encoding]::UTF8.GetBytes($workspacePath)
    $workspaceHash = [System.BitConverter]::ToString(
        $sha256.ComputeHash($workspaceBytes)
    ).Replace("-", "").ToLowerInvariant()
}
finally {
    $sha256.Dispose()
}

$instanceMutex = New-Object System.Threading.Mutex(
    $false,
    "Local\ProjectL-$workspaceHash"
)
$ownsMutex = $false
try {
    try {
        $ownsMutex = $instanceMutex.WaitOne(0, $false)
    }
    catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
    }

    if (-not $ownsMutex) {
        Write-Warning "Project L is already running from this workspace."
        exit 3
    }

    & $venvPython (Join-Path $PSScriptRoot "bot.py") @BotArguments
    $botExit = $LASTEXITCODE
}
finally {
    if ($ownsMutex) {
        $instanceMutex.ReleaseMutex()
    }
    $instanceMutex.Dispose()
}

exit $botExit
