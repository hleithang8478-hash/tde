@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\.."
echo [ems_start_all] 工作目录: %CD%
powershell -ExecutionPolicy Bypass -File "%~dp0ems_start_all.ps1" %*
endlocal
pause
