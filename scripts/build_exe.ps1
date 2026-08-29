[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$buildRequirements = Join-Path $projectRoot "requirements-build.txt"
$specPath = Join-Path $projectRoot "RSViewer.spec"
$distRoot = Join-Path $projectRoot "dist"
$outputPath = Join-Path $distRoot "RSViewer.exe"
$runtimeDataPath = Join-Path $distRoot "data"
$runtimeDataBackupRoot = Join-Path $projectRoot "build\dist-data-backups"

function Move-RuntimeDataOutOfDistribution {
    if (-not (Test-Path -LiteralPath $runtimeDataPath)) {
        return
    }

    $expectedRuntimeDataPath = [System.IO.Path]::GetFullPath(
        (Join-Path ([System.IO.Path]::GetFullPath($distRoot)) "data")
    )
    $resolvedRuntimeDataPath = [System.IO.Path]::GetFullPath($runtimeDataPath)
    if (-not [string]::Equals(
        $resolvedRuntimeDataPath,
        $expectedRuntimeDataPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to move unexpected runtime data path: $resolvedRuntimeDataPath"
    }

    New-Item -ItemType Directory -Path $runtimeDataBackupRoot -Force | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $backupPath = Join-Path $runtimeDataBackupRoot $timestamp
    Move-Item -LiteralPath $resolvedRuntimeDataPath -Destination $backupPath
    Write-Host "Preserved existing dist\data at $backupPath"
}

Set-Location -LiteralPath $projectRoot
Move-RuntimeDataOutOfDistribution

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        & $launcher.Source -3 -m venv (Join-Path $projectRoot ".venv")
    } else {
        $python = Get-Command python.exe -ErrorAction Stop
        & $python.Source -m venv (Join-Path $projectRoot ".venv")
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python virtual environment."
    }
}

& $venvPython -c "import PyInstaller, PySide6, qfluentwidgets, requests, bs4, lxml" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing build dependencies..."
    & $venvPython -m pip install --disable-pip-version-check -r $buildRequirements
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install build dependencies."
    }
}

Write-Host "Building RSViewer.exe..."
& $venvPython -m PyInstaller --noconfirm --clean $specPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
    throw "PyInstaller completed without creating dist\RSViewer.exe."
}

$archiveListing = & $venvPython -m PyInstaller.utils.cliutils.archive_viewer -l $outputPath 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect the generated PyInstaller archive."
}
if ($archiveListing | Select-String -Pattern "(?i)rsviewer\.db|config\.json") {
    throw "Generated executable unexpectedly contains writable RSViewer state."
}

$packagedStateFiles = @(
    Get-ChildItem -LiteralPath $distRoot -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -ieq "rsviewer.db" -or $_.Name -ieq "config.json"
        }
)
if ($packagedStateFiles.Count -gt 0) {
    $paths = ($packagedStateFiles.FullName -join ", ")
    throw "Distribution directory unexpectedly contains writable state: $paths"
}

Write-Host "Created $outputPath"
