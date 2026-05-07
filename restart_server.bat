@echo off
setlocal

cd /d "%~dp0"

set "PERFECT_PIXEL_ROOT=%CD%"
set "PERFECT_PIXEL_HOST=127.0.0.1"
set "PERFECT_PIXEL_PORT=8765"

if not "%~1"=="" (
  set "PERFECT_PIXEL_PORT=%~1"
)

set "PERFECT_PIXEL_PY=%PERFECT_PIXEL_ROOT%\.venv\Scripts\python.exe"
if not exist "%PERFECT_PIXEL_PY%" (
  set "PERFECT_PIXEL_PY=python"
)

echo.
echo [1/2] Stopping Perfect Pixel project processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = [IO.Path]::GetFullPath($env:PERFECT_PIXEL_ROOT); $targets = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($root) -and $_.ProcessId -ne $PID -and $_.Name -match '^(python|pythonw|py|node|npm)(\.exe)?$' }); if ($targets.Count -eq 0) { Write-Host 'No matching project process found.' } else { foreach ($proc in $targets) { Write-Host ('Killing PID {0}: {1}' -f $proc.ProcessId, $proc.CommandLine); Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue } }"

echo.
echo [2/2] Starting Perfect Pixel server on http://%PERFECT_PIXEL_HOST%:%PERFECT_PIXEL_PORT%/
powershell -NoProfile -ExecutionPolicy Bypass -Command "$argsList = @('tools\server.py', '--host', $env:PERFECT_PIXEL_HOST, '--port', $env:PERFECT_PIXEL_PORT); Start-Process -FilePath $env:PERFECT_PIXEL_PY -ArgumentList $argsList -WorkingDirectory $env:PERFECT_PIXEL_ROOT -WindowStyle Hidden"

echo Done.
echo URL: http://%PERFECT_PIXEL_HOST%:%PERFECT_PIXEL_PORT%/

endlocal
