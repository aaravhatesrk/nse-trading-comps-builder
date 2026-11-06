# Starts the comps helper from this folder. Safe to run twice: does nothing if it is already listening.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Get-NetTCPConnection -LocalPort 5057 -State Listen -ErrorAction SilentlyContinue) { exit 0 }
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
& "$root\.venv\Scripts\python.exe" -m helper.server
