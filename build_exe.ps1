$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Create .venv and install requirements.txt first."
}

& $python -m PyInstaller --noconfirm --clean (Join-Path $projectRoot 'estamp_automation.spec')
Write-Host "Executable created at dist\Compitcom-eStamp-Automation.exe"

