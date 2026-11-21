[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = "1"

$configurePath = Join-Path $PSScriptRoot "configure_gui.py"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $venvPython) {
    & $venvPython $configurePath
    exit $LASTEXITCODE
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.11 $configurePath
    exit $LASTEXITCODE
}

throw "Python 3.11 is required. Install it from python.org, then run .\setup.ps1."
