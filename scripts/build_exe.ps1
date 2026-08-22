[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$buildRequirements = Join-Path $projectRoot "requirements-build.txt"
$specPath = Join-Path $projectRoot "RSViewer.spec"
$outputPath = Join-Path $projectRoot "dist\RSViewer.exe"

Set-Location -LiteralPath $projectRoot

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

Write-Host "Created $outputPath"
