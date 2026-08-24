$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Create .venv and install requirements.txt first."
}

& $python -m PyInstaller --noconfirm --clean (Join-Path $projectRoot 'estamp_automation.spec')
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

& (Join-Path $projectRoot 'scripts\validate_windows_dist.ps1')
if ($LASTEXITCODE -ne 0) {
    throw "The packaged application failed validation."
}
Write-Host "Windows package created at dist\Compitcom-eStamp-Automation"

