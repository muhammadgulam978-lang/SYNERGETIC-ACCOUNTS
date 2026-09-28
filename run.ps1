$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Local environment is missing. Run .\setup.ps1 first.'
}
& '.venv\Scripts\python.exe' manage.py runserver 127.0.0.1:8090
