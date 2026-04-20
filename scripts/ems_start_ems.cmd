@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [ems] 工作目录: %CD%
echo [ems] 启动 EMS 主循环（按 Ctrl+C 停止）...
python run_ems.py
pause
