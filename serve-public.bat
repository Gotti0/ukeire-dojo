@echo off
rem ukeire-dojo: start the password-protected trainer server (0.0.0.0:5173).
rem Double-click to start. Close this window or press Ctrl+C to stop.
rem Extra arguments are passed to scripts\serve-public.ps1, e.g. serve-public.bat -Port 5180
setlocal
cd /d "%~dp0"
title ukeire-dojo server

where pwsh >nul 2>nul
if %errorlevel%==0 (
  set "PS=pwsh"
) else (
  set "PS=powershell"
)

rem Bypass applies to this one process only; the script is unsigned.
%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\serve-public.ps1" %*
if errorlevel 1 (
  echo.
  echo The server stopped with an error. See data\server-access.log
  pause
)
endlocal
