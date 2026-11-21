[CmdletBinding()]
param(
    [switch]$NoGui,
    [switch]$NoRestart,
    [switch]$NoRuntimeLog,
    [ValidateRange(0, 20)]
    [int]$MaxRestarts = 5,
    [ValidateRange(1, 300)]
    [int]$RestartDelaySeconds = 5,
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
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
$backupPath = Join-Path $PSScriptRoot "runtime_backup.py"

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

    Write-Host "Creating a verified runtime database backup..."
    & $venvPython $backupPath backup --keep 14 --quiet
    $backupExit = $LASTEXITCODE
    if ($backupExit -ne 0) {
        Write-Warning "Database backup failed; the bot was not started."
        exit $backupExit
    }

    $transcriptStarted = $false
    if (-not $NoRuntimeLog) {
        $dataDirectory = Join-Path $PSScriptRoot "Data"
        if (-not (Test-Path -LiteralPath $dataDirectory -PathType Container)) {
            New-Item -ItemType Directory -Path $dataDirectory | Out-Null
        }
        Get-ChildItem -LiteralPath $dataDirectory -Filter "bot-runtime-*.log" -File |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -Skip 9 |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
        $runtimeTimestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $runtimeLog = Join-Path $dataDirectory "bot-runtime-$runtimeTimestamp.log"
        try {
            Start-Transcript -LiteralPath $runtimeLog -Append | Out-Null
            $transcriptStarted = $true
            Write-Host "Runtime output is also being saved to $runtimeLog"
        }
        catch {
            Write-Warning "Runtime logging could not start: $($_.Exception.Message)"
        }
    }

    $restartLimit = if ($NoRestart) { 0 } else { $MaxRestarts }
    $restartCount = 0
    $botExit = 1
    try {
        while ($true) {
            & $venvPython (Join-Path $PSScriptRoot "bot.py") @BotArguments
            $botExit = $LASTEXITCODE
            if ($botExit -eq 0 -or $botExit -in @(2, 3, 4)) {
                break
            }
            if ($restartCount -ge $restartLimit) {
                Write-Warning "Project L exited with code $botExit and reached the restart limit."
                break
            }
            $restartCount += 1
            Write-Warning "Project L exited unexpectedly with code $botExit. Restarting in $RestartDelaySeconds second(s) ($restartCount/$restartLimit)..."
            Start-Sleep -Seconds $RestartDelaySeconds
        }
    }
    finally {
        if ($transcriptStarted) {
            Stop-Transcript | Out-Null
        }
    }
}
finally {
    if ($ownsMutex) {
        $instanceMutex.ReleaseMutex()
    }
    $instanceMutex.Dispose()
}

exit $botExit
