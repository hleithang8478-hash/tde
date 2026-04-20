@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [ems] 工作目录: %CD%
echo [ems] 启动 api_server（按 Ctrl+C 停止）...
python api_server.py
pause
