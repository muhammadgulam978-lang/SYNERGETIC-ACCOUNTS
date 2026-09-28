$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
}
& '.venv\Scripts\python.exe' -m pip install --disable-pip-version-check -r requirements.txt
& '.venv\Scripts\python.exe' manage.py migrate --noinput
& '.venv\Scripts\python.exe' manage.py bootstrap_accounts
Write-Host 'Synergetic Accounts setup complete.' -ForegroundColor Green
Write-Host 'Run .\run.ps1 then open http://127.0.0.1:8090' -ForegroundColor Cyan
